
## install

```
# 要求 python 大于 3.11
python -m pip install .

# 要求 VisualStudio 安装 MSVC v143 - VS 2022 C++ x64/x86 Spectre 缓解库(最新)
```

### i18n

```
git fetch origin i18n

# 把 i18n 分支里的翻译文件和禁止翻译配置取到当前工作区
git checkout origin/i18n -- i18n
git checkout origin/i18n -- config/do_not_translate.json

# 查看有哪些版本
git ls-tree --name-only origin/i18n:i18n

# 然后按对应版本克隆 Zed，建议 Zed 版本和翻译目录一致
git clone --branch v1.5.3-pre https://github.com/zed-industries/zed.git zed
```


## build

```
# 替换品牌名 可选
python scripts\rebrand.py --zed-dir zed

# 替换翻译文件
zedl10n replace --input i18n/v1.5.3-pre/zh-CN.json --source-root zed --do-not-translate config/do_not_translate.json
# 应用一些代码补丁
python patch_agent_env.py --source-root zed

# 开始 build
.\build-zed.cmd
```
