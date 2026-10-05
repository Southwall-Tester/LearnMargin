# 安全边界与修复记录

LearnMargin 是单用户本机工具。材料由用户导入，用户指定模型服务；指定服务能够看到被发送的材料。应用没有公网多用户认证，不应通过反向代理直接公开。

## 本轮确认的问题

| 问题 | 触发条件与影响 | 处理 |
|---|---|---|
| ZIP 只相信中央目录大小 | 导入伪造大小的 Office/EPUB 包，检查可能先大量解压再发现 CRC 错误；伪造截断后 CRC 还可能让库接受截断内容 | 从实际 STORE/DEFLATE 压缩流逐块限量读取，核对真实长度、流结束、CRC 和总预算，再交给文档库 |
| XML 声明只按 ASCII 字节拦截 | UTF-16 等编码可越过声明过滤；未证实可利用的外部实体文件读取 | XML 解析器在识别编码后拒绝 DOCTYPE、ENTITY、外部实体；检查 OOXML 内容类型指定的 XML 部件 |
| 上传限制晚于 multipart 解析 | 超大请求可能先进入框架临时文件，再触发文件大小检查 | JSON 256 KiB，上传体 50 MiB 加 64 KiB 表单开销；按实际接收字节限量，无长度或伪造长度也受限；单次仅一文件、无额外字段 |
| 解析线程不可终止 | 恶意或异常原生解析可以持续占用服务资源 | 独立解析进程，150 秒与 2 GiB 上限；超时或取消先结束该进程树再清理 |
| 本机 LibreOffice 不构成文档沙箱 | 转换器和子进程原本以当前用户权限处理文件；此前仅默认关闭，启用后仍未隔离 | 启用后也强制 Windows 专用 WSL 2 / Linux bubblewrap，限制文件访问及网络；沙箱失败直接拒绝，无普通转换回退 |
| API 地址中的凭据可能进浏览器偏好 | 用户把 key 填入查询参数/片段，前端可先保存、后端才拒绝 | 地址校验与后端一致，拒绝账号、查询参数、片段及控制字符；读取旧偏好时清理不安全值 |
| 模型响应未限制总体积与总时间 | 自定义上游持续输出或返回超大 JSON，可能耗尽资源 | 流式 8 MiB 上限，完整请求含重试共用总期限，JSON 深度上限；错误响应不读取正文，不跟随重定向 |
| 上游 usage 字段名可能进入记录 | 上游在用量字段名中夹带敏感文本 | 只保存已知用量字段的有限非负数值，不回显上游错误正文或底层异常 |
| 代理配置异常回显凭据 | HTTPS 代理地址无效时，HTTP 客户端构造错误可能带出代理用户名或凭据 | 初始化错误转换为固定提示，隐藏底层异常链；保留正常 HTTPS 代理支持 |
| 本地目录重定向缺少文件级验证 | 有权修改数据目录的本地程序创建链接后，读写可能越过边界；未发现上传可创建该链接的远程链 | 拒绝资料/任务目录及元数据重定向；下载与来源读取也检查文件路径；并发元数据读写互斥 |

## 额外加固

- 校验实际连接为 loopback；API 拒绝不匹配的 Origin 和跨站 Fetch Metadata，含读取接口；同源 iframe 仍可用。普通本机 CLI 客户端可以不发送浏览器头，不因此获得公网使用权限。
- 上传与连接测试分别最多两项同时执行，读取请求体最多 60 秒。响应禁止跨站资源嵌入与外站 iframe 嵌套，敏感 API 不缓存。
- 新生成 HTML 的 CSP 只允许两个随软件提供的脚本哈希；用户内容仍经过 Markdown HTML 禁用、Jinja 转义和 KaTeX `trust=false`。本轮未复现可利用的正文 XSS，不把加固误报成已发现 XSS。
- PDF Chromium 显式启用系统沙箱，无法启动时失败，不自动使用 `--no-sandbox`。生成过程中不加载外部资源。
- 本机明文 HTTP 模型请求不使用环境代理；远程 HTTPS 保留用户配置的代理与默认 TLS 验证。API 地址不从材料或模型输出中获得。

## 旧 Office 转换的系统隔离

本机开关只启用隔离转换，不授权不安全运行。材料以字节复制或只读挂载送入沙箱，原生 LibreOffice 解析在沙箱建立后开始。沙箱权限、资源配置或启动失败均拒绝转换；macOS 未提供对应后端。默认不启动已有的用户 Office 会话，不继承 API 密钥、代理凭据或任意文件句柄。

| 平台 | 文件与网络权限 | 临时资源 |
|---|---|---|
| Windows | 固定非 root 用户运行专用 WSL 2 发行版中的可信桥接，再为每次转换建立与 Linux 相同的 bubblewrap 沙箱；通过管道传材料字节，不传 Windows 文件路径，不挂 Windows 盘、桌面、WSLg 或 interop 通道 | Windows Job 管理宿主 worker 与 wsl.exe；Linux 桥接独立监测 EOF 和 3 秒心跳失联，终止沙箱整树并清理内存临时材料。不能把 wsl.exe 退出视为 Linux 进程已经退出 |
| Linux | bubblewrap 建立 user/PID/network/IPC/UTS namespace，清空环境与 capabilities，禁止再次建立 user namespace；仅映射必需系统运行资源和单份只读输入，没有宿主可写挂载 | 临时写入使用限额 tmpfs，转换结果通过最多 50 MiB 的管道返回；使用单进程资源限制和总墙钟期限 |

转换结束后，宿主仅接收大小受限、带 PDF 文件头的结果，按新文件独占创建；结果仍是不可信内容。平台实现还检查输出文件类型及重定向，结束沙箱进程树后再向通用 PDF 解析流程交付。运行库及系统仍需要安全更新，权限隔离不意味着没有内核或沙箱漏洞。

Windows 的专用发行版需通过显式本机命令 `learnmargin --setup-office` 配置。脚本校验官方基础镜像 SHA-256，拒绝覆盖已有同名发行版及目录，禁用该发行版的 Windows 盘自动挂载与 interop，安装非 root 用户和 root 持有的环境标记。每次转换仍重新验证环境并建立 bubblewrap；一个发行版由多次任务复用，不是每次新建虚拟机。参考：[WSL 官方文档](https://learn.microsoft.com/en-us/windows/wsl/)、[bubblewrap 安全模型](https://github.com/containers/bubblewrap/blob/main/README.md)。

Ubuntu 若阻止创建用户命名空间，应由管理员启用发行版提供的 bwrap 专用 AppArmor 策略，保留已有自定义策略与全局限制。Ubuntu 24.04 的 `apparmor-profiles` 包提供 `bwrap-userns-restrict`；CI 仅在临时机器加载该发行版策略，不全局关闭 AppArmor。参见 [Ubuntu 安全团队说明](https://discourse.ubuntu.com/t/understanding-apparmor-user-namespace-restriction/58007)及[发行版包文件清单](https://packages.ubuntu.com/noble-updates/all/apparmor-profiles/filelist)。

桥接只接收限定长度、限定格式的材料，清除凭据和 `WSLENV`。材料先进入 Linux `/dev/shm` 私有目录，再只读挂入转换沙箱；原生文档解析不会在桥接中执行。心跳失联和输出堵塞均走同一终止清理路径。整个 WSL 实例或系统崩溃时不能保证 Python 清理代码执行，内存临时数据随该实例关闭消失。

## 其他边界

上述系统隔离覆盖旧 Office 的 LibreOffice 转换。通用 PDF、图像、DOCX/PPTX 等解析仍在有资源限制的提取 worker 中；Office 转换后的 PDF 也经过这一通道。因此不能把转换器的权限隔离表述为整个文档解析链都已进入 OS 沙箱，也不能承诺任意恶意文件绝对安全。没有证据把此前 XML 过滤绕过定性为已经实现 XXE。

Windows Job Object 限制提交内存和子进程树，Unix 使用进程组及每进程资源上限；Linux 的 2 GiB 不是所有子进程合计的 cgroup 配额，两者不能视为等价资源限制。已生成的旧 HTML 不会自动重写。没有进行真实付费模型调用、未知漏洞利用测试或所有模型网关兼容验证。

依赖审计覆盖锁定的 Python 包与 npm 依赖的已公布漏洞，不覆盖所有随包携带的原生库、系统 Python/Expat、操作系统和浏览器。Python 官方说明了 [XML 解析与 Expat 的安全边界](https://docs.python.org/3/library/xml.html#xml-security)；运行环境同样需要维护。

Ubuntu 23.10 及以后对浏览器 user namespace 有额外限制。CI 为本次安装的两个 Playwright 浏览器的准确路径加载专用 AppArmor 配置，只放行它们创建 namespace；不用通配符覆盖其他程序，不全局关闭 AppArmor，也不传入 `--no-sandbox`。该配置只在临时 CI 机器生效。本机遇到同类启动错误时，参见 [Chromium 官方沙箱配置说明](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md)，配置系统支持的沙箱后再运行。

## 可复核验证

回归均使用合成文件和 mock API，无私人材料或真实密钥：ZIP 声明 100 字节但实际 8 MiB（包括伪造 CRC），不同编码与 OOXML 自定义后缀的声明，真实目录 junction，上传体边界和取消，模型响应膨胀/慢速输出，凭据反射，脚本/事件处理器与联网、本地文件请求阻断。实际 A4 PDF 检查公式、侧栏和内部链接。

验证结果与未执行项见 [VALIDATION.md](VALIDATION.md)。提交到仓库的测试在 `tests/test_http_security.py`、`tests/test_extraction_worker.py`、`tests/test_linux_sandbox.py`、`tests/test_wsl_sandbox.py`、`tests/test_wsl_worker.py`、`tests/test_ingestion.py`、`tests/test_storage_security.py`、`tests/test_provider.py`、`tests/test_config.py`、`tests/test_rendering.py` 和前端测试中。
