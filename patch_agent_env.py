#!/usr/bin/env python3
"""
编译前补丁脚本：修复 Agent 插件环境变量被覆盖问题，并规避 GitHub API 限流。

补丁点 1: 删除强制清空 ANTHROPIC_API_KEY 的代码（兼容新旧两种位置）
补丁点 2: 透传 Claude Code 相关系统环境变量（兼容新旧两种文件）
补丁点 3: latest_github_release 改为读取 github.com 页面，避免 api.github.com rate limit

用法: python3 patch_agent_env.py [--source-root zed] [--dry-run]
"""

import argparse
import io
import sys
from pathlib import Path

# Windows CI 默认 cp1252 编码，无法输出中文
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

PATCH_MARKER = "[ZED_GLOBALIZATION_PATCH]"

# 需要透传的环境变量注入代码（Rust）
ENV_PASSTHROUGH_SNIPPET = """\
// {marker} 透传 Claude Code 相关系统环境变量
        for var_name in [
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX",
        ] {{
            if let Ok(val) = std::env::var(var_name) {{
                extra_env.insert(var_name.into(), val);
            }}
        }}
        for (key, val) in std::env::vars() {{
            if key.starts_with("AWS_")
                || key.starts_with("GOOGLE_CLOUD_")
                || key == "CLOUD_ML_REGION"
            {{
                extra_env.insert(key, val);
            }}
        }}"""

GITHUB_RELEASE_PAGE_HELPERS = """\
// [ZED_GLOBALIZATION_PATCH] 通过 github.com 页面获取 release，避免 api.github.com 限流
struct GithubReleaseSummary {
    tag_name: String,
    pre_release: bool,
}

async fn fetch_github_page(
    url: &str,
    http: Arc<dyn HttpClient>,
    context: &'static str,
) -> anyhow::Result<String> {
    let request = Request::get(url)
        .header("Accept", "text/html")
        .header("User-Agent", "Zed")
        .follow_redirects(crate::RedirectPolicy::FollowAll)
        .body(Default::default())?;

    let mut response = http.send(request).await.context(context)?;

    let mut body = Vec::new();
    response
        .body_mut()
        .read_to_end(&mut body)
        .await
        .context("error reading GitHub page")?;

    if response.status().is_client_error() {
        let text = String::from_utf8_lossy(body.as_slice());
        bail!("状态错误 {}, 响应: {text:?}", response.status().as_u16());
    }

    Ok(String::from_utf8_lossy(body.as_slice()).into_owned())
}

fn github_repo_url(repo_name_with_owner: &str, extra_segments: &[&str]) -> Result<String> {
    let mut url = Url::parse("https://github.com")?;
    {
        let mut segments = url
            .path_segments_mut()
            .map_err(|()| anyhow!("cannot modify url path segments"))?;
        for segment in repo_name_with_owner.split('/') {
            segments.push(segment);
        }
        for segment in extra_segments {
            segments.push(segment);
        }
    }
    Ok(url.to_string())
}

fn percent_decode(input: &str) -> String {
    let mut bytes = Vec::with_capacity(input.len());
    let mut chars = input.as_bytes().iter().copied();
    while let Some(byte) = chars.next() {
        if byte == b'%' {
            let Some(high) = chars.next().and_then(hex_value) else {
                bytes.push(byte);
                continue;
            };
            let Some(low) = chars.next().and_then(hex_value) else {
                bytes.push(byte);
                continue;
            };
            bytes.push(high << 4 | low);
        } else {
            bytes.push(byte);
        }
    }
    String::from_utf8_lossy(&bytes).into_owned()
}

fn hex_value(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        b'A'..=b'F' => Some(byte - b'A' + 10),
        _ => None,
    }
}

fn html_unescape(input: &str) -> String {
    input
        .replace("&amp;", "&")
        .replace("&quot;", "\"")
        .replace("&#39;", "'")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
}

fn parse_release_summaries(repo_name_with_owner: &str, html: &str) -> Vec<GithubReleaseSummary> {
    let needle = format!("/{repo_name_with_owner}/releases/tag/");
    let mut summaries = Vec::new();
    let mut seen = HashSet::new();
    let mut offset = 0;

    while let Some(relative_start) = html[offset..].find(&needle) {
        let start = offset + relative_start + needle.len();
        let end = html[start..]
            .find(|character| matches!(character, '"' | '?' | '#'))
            .map_or(html.len(), |end| start + end);
        offset = end;

        let tag_name = percent_decode(&html[start..end]);
        if tag_name.is_empty() || !seen.insert(tag_name.clone()) {
            continue;
        }

        let next_release = html[offset..]
            .find(&needle)
            .map_or(html.len(), |next| offset + next);
        let release_html = &html[start..next_release];
        summaries.push(GithubReleaseSummary {
            tag_name,
            pre_release: release_html.contains("Pre-release"),
        });
    }

    summaries
}

fn parse_release_assets(
    repo_name_with_owner: &str,
    tag: &str,
    html: &str,
) -> Vec<GithubReleaseAsset> {
    let download_path = format!("/{repo_name_with_owner}/releases/download/{tag}/");
    let mut assets = Vec::new();
    let mut seen = HashSet::new();
    let mut offset = 0;

    while let Some(relative_index) = html[offset..].find(&download_path) {
        let index = offset + relative_index;
        offset = index + download_path.len();

        let href_start = html[..index]
            .rfind("href=\"")
            .map(|href_start| href_start + "href=\"".len())
            .unwrap_or(index);
        let href_end = html[index..]
            .find('"')
            .map(|href_end| index + href_end)
            .unwrap_or(index);
        let href = html_unescape(&html[href_start..href_end]);

        if !seen.insert(href.clone()) {
            continue;
        }

        let download_url = if href.starts_with("http://") || href.starts_with("https://") {
            href.clone()
        } else {
            format!("https://github.com{href}")
        };
        let Some(download_path_index) = href.find(&download_path) else {
            continue;
        };
        let name = href[download_path_index + download_path.len()..]
            .split(['?', '#'])
            .next()
            .map(percent_decode)
            .unwrap_or_default();
        let asset_html = html[index..].split("</li>").next().unwrap_or_default();
        let digest = asset_html.find("sha256:").and_then(|digest_start| {
            let digest_start = digest_start + "sha256:".len();
            let digest: String = asset_html[digest_start..]
                .chars()
                .take_while(|character| character.is_ascii_hexdigit())
                .collect();
            (digest.len() == 64).then_some(digest)
        });

        assets.push(GithubReleaseAsset {
            name,
            browser_download_url: download_url,
            digest,
        });
    }

    assets
}
"""

GITHUB_RELEASE_PAGE_FUNCTION = """\
pub async fn latest_github_release(
    repo_name_with_owner: &str,
    require_assets: bool,
    pre_release: bool,
    http: Arc<dyn HttpClient>,
) -> anyhow::Result<GithubRelease> {
    let releases_url = github_repo_url(repo_name_with_owner, &["releases"])?;
    let releases_html = fetch_github_page(
        &releases_url,
        http.clone(),
        "error fetching GitHub releases page",
    )
    .await?;

    let summaries = parse_release_summaries(repo_name_with_owner, &releases_html);

    for summary in summaries
        .into_iter()
        .filter(|summary| summary.pre_release == pre_release)
    {
        let assets_url = github_repo_url(
            repo_name_with_owner,
            &["releases", "expanded_assets", &summary.tag_name],
        )?;
        let assets_html = fetch_github_page(
            &assets_url,
            http.clone(),
            "error fetching GitHub release assets page",
        )
        .await?;
        let assets = parse_release_assets(repo_name_with_owner, &summary.tag_name, &assets_html);

        if require_assets && assets.is_empty() {
            continue;
        }

        log::info!(
            "fetched latest GitHub release for {repo_name_with_owner} from github.com page: {}",
            summary.tag_name
        );
        let mut release = GithubRelease {
            tarball_url: build_asset_url(
                repo_name_with_owner,
                &summary.tag_name,
                AssetKind::TarGz,
            )?,
            zipball_url: build_asset_url(repo_name_with_owner, &summary.tag_name, AssetKind::Zip)?,
            tag_name: summary.tag_name,
            pre_release: summary.pre_release,
            assets,
        };

        release.assets.iter_mut().for_each(|asset| {
            if let Some(digest) = &mut asset.digest
                && let Some(stripped) = digest.strip_prefix("sha256:")
            {
                *digest = stripped.to_owned();
            }
        });
        return Ok(release);
    }

    bail!("finding a prerelease")
}
"""


def _read(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _write(path: Path, content: str, dry_run: bool, name: str) -> None:
    if dry_run:
        print(f"  DRY-RUN: {name} 将被修改")
    else:
        path.write_text(content, encoding="utf-8")
        print(f"  OK: {name} 补丁成功")


def patch_remove_api_key_clear(source_root: Path, dry_run: bool) -> bool:
    """补丁点 1: 删除强制清空 ANTHROPIC_API_KEY 的代码行。

    旧版在 agent_server_store.rs，新版在 custom.rs (CLAUDE_AGENT_NAME 分支)。
    """
    candidates = [
        source_root / "crates/project/src/agent_server_store.rs",
        source_root / "crates/agent_servers/src/custom.rs",
    ]
    old_line = 'extra_env.insert("ANTHROPIC_API_KEY".into(), "".into());'
    # 旧版写法用 env 而非 extra_env
    old_line_legacy = 'env.insert("ANTHROPIC_API_KEY".into(), "".into());'

    for target in candidates:
        name = target.name
        content = _read(target)
        if content is None:
            continue
        if PATCH_MARKER in content and "已删除强制清空 ANTHROPIC_API_KEY" in content:
            print(f"  SKIP: {name} 已包含补丁标记，跳过")
            return True

        for needle in (old_line, old_line_legacy):
            if needle in content:
                replacement = f"// {PATCH_MARKER} 已删除强制清空 ANTHROPIC_API_KEY"
                patched = content.replace(needle, replacement, 1)
                _write(target, patched, dry_run, name)
                return True

    print("  WARN: 未找到强制清空 ANTHROPIC_API_KEY 的代码，上游可能已修改，跳过")
    return False


def patch_env_passthrough(source_root: Path, dry_run: bool) -> bool:
    """补丁点 2: 在 connect() 中透传系统环境变量。

    旧版 claude.rs + custom.rs 并存，新版仅 custom.rs。对所有匹配文件注入。
    """
    candidates = [
        source_root / "crates/agent_servers/src/claude.rs",
        source_root / "crates/agent_servers/src/custom.rs",
    ]
    anchor = "let mut extra_env = load_proxy_env(cx);"
    anchor_legacy = "let extra_env = load_proxy_env(cx);"
    patched_any = False

    for target in candidates:
        name = target.name
        content = _read(target)
        if content is None:
            continue
        if PATCH_MARKER in content and "透传 Claude Code" in content:
            print(f"  SKIP: {name} 已包含补丁标记，跳过")
            patched_any = True
            continue

        for needle in (anchor, anchor_legacy):
            if needle not in content:
                continue
            new_anchor = "let mut extra_env = load_proxy_env(cx);"
            inject = ENV_PASSTHROUGH_SNIPPET.format(marker=PATCH_MARKER)
            replacement = f"{new_anchor}\n{inject}"
            patched = content.replace(needle, replacement, 1)
            _write(target, patched, dry_run, name)
            patched_any = True
            break

    if not patched_any:
        print("  WARN: 未找到 load_proxy_env 调用，上游可能已修改，跳过")
    return patched_any


def _replace_between(content: str, start_marker: str, end_marker: str, replacement: str) -> str | None:
    start = content.find(start_marker)
    if start == -1:
        return None
    end = content.find(end_marker, start)
    if end == -1:
        return None
    return content[:start] + replacement + content[end:]


def patch_github_release_page_scrape(source_root: Path, dry_run: bool) -> bool:
    """补丁点 3: latest_github_release 改为读取 github.com 页面。

    这个补丁保留对外函数签名和返回结构不变，只替换底层最新 release 查询方式。
    """
    target = source_root / "crates/http_client/src/github.rs"
    name = "github.rs"
    content = _read(target)
    if content is None:
        print(f"  WARN: 未找到 {target}，跳过")
        return False

    if "fetch_github_page(" in content and "parse_release_assets(" in content:
        print(f"  SKIP: {name} 已包含 github.com 页面解析补丁，跳过")
        return True

    patched = content.replace(
        "use std::sync::Arc;",
        "use std::{collections::HashSet, sync::Arc};",
        1,
    )
    if patched == content:
        print("  WARN: 未找到 std::sync::Arc import，上游可能已修改，跳过")
        return False

    insertion_anchor = "pub async fn latest_github_release("
    if insertion_anchor not in patched:
        print("  WARN: 未找到 latest_github_release，上游可能已修改，跳过")
        return False
    patched = patched.replace(insertion_anchor, GITHUB_RELEASE_PAGE_HELPERS + "\n" + insertion_anchor, 1)

    patched = _replace_between(
        patched,
        "pub async fn latest_github_release(",
        "pub async fn get_release_by_tag_name(",
        GITHUB_RELEASE_PAGE_FUNCTION + "\n",
    )
    if patched is None:
        print("  WARN: 无法定位 latest_github_release 函数边界，上游可能已修改，跳过")
        return False

    _write(target, patched, dry_run, name)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="编译前补丁：修复 Agent 插件环境变量被覆盖问题，并规避 GitHub API 限流"
    )
    parser.add_argument(
        "--source-root",
        default="zed",
        help="Zed 源码根目录（默认: zed）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅检查，不实际修改文件",
    )
    args = parser.parse_args()

    source_root = Path(args.source_root)
    if not source_root.is_dir():
        print(f"ERROR: 源码目录 {source_root} 不存在")
        return 1

    print(f"源码目录: {source_root.resolve()}")
    if args.dry_run:
        print("模式: dry-run（不修改文件）\n")
    else:
        print("模式: 正式补丁\n")

    print("[补丁 1] 删除强制清空 ANTHROPIC_API_KEY")
    r1 = patch_remove_api_key_clear(source_root, args.dry_run)

    print("[补丁 2] 透传 Claude Code 相关系统环境变量")
    r2 = patch_env_passthrough(source_root, args.dry_run)

    print("[补丁 3] latest_github_release 改为读取 github.com 页面")
    r3 = patch_github_release_page_scrape(source_root, args.dry_run)

    print()
    if r1 and r2 and r3:
        print("全部补丁已就绪。")
        return 0
    else:
        print("部分补丁未能应用，请检查上方 WARN 信息。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
