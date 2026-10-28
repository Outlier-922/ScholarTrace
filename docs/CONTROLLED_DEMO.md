# 受控共享演示：本机启动与公网准备（0.10.0）

这版增加访问认证和演示限制，仍是**一个账号、一个共享工作区**。不同体验者能看到同一批文献和结果；不提供个人账号注册、用户文件隔离或管理员/评审分权。首次配置默认只读、模型额度为0，先准备独立演示素材再开放交互。

## 本机操作

前后端依赖按README安装。本机生产前端已构建；重新修改界面后应在apps/web执行`pnpm build`。以下启动只监听127.0.0.1，不会自动发布到公网。

1. 双击项目根目录“配置演示访问.cmd”。地址回车默认`http://127.0.0.1:8000`，用户名回车默认`reviewer`；输入12-256字符的专用密码并确认，输入不会显示。
2. 向导新建私有`.env.demo`，只存密码哈希，不打印密码或哈希，已有文件则停止，防止覆盖。请自行记住密码，勿发到聊天或公开仓库。
3. 双击“启动受控演示.cmd”，打开上面的**干净地址**，在浏览器认证框填写账号密码。不要把账号密码拼进URL，带凭据URL会使浏览器相对fetch失败，也会留下凭据记录。
4. 当前是只读浏览，空演示库不会自动复制开发文献。关闭启动窗口或Ctrl+C停止；数据保存在`data/demo_workspace`，重启继续使用。不要同时用开发启动器操作同一数据目录。

命令行等价入口：

~~~powershell
.venv312/Scripts/python.exe -X utf8 scripts/configure_demo.py
.venv312/Scripts/python.exe -X utf8 scripts/serve_demo.py --check-config
.venv312/Scripts/python.exe -X utf8 scripts/serve_demo.py
~~~

## 预导入和交互演示

停止服务，在本机编辑`.env.demo`，把`SCHOLARTRACE_DEMO_ALLOW_WRITES`改为`true`，重新启动，上传允许演示的PDF并等待解析完成。准备完后可以改回`false`再重启；只读时所有HTTP写操作（包括提问、搜索提交、翻译和任务创建）被拒绝，适合展示已保存素材和产物。它不是提供全部功能的现场交互模式。

如需现场模型问答，可在这个私有文件中本地填写LLM_BASE_URL、LLM_API_KEY、LLM_MODEL，另设非零模型额度；配置步骤见MODEL_SETUP.md。向导故意写入三个空值，防止从开发.env继承密钥。建议先验证无模型证据流程，再由你明确设置可承担的额度，不在公共说明中粘贴真实密钥。

| 配置 | 默认值 | 实际含义 |
|---|---:|---|
| DEMO_ALLOW_WRITES | false | 拒绝POST/PATCH/DELETE等写请求；暂停自动追踪、翻译/任务/解析恢复 |
| DEMO_REQUESTS_PER_MINUTE | 360 | 所有通过认证的资源共享60秒滑动请求窗口；包含API、页面和静态资源 |
| DEMO_FAILED_LOGINS_PER_MINUTE | 10 | 每个直接对端失败认证窗口；反代下体验者可能共用对端，触发后需等60秒 |
| DEMO_MAX_REQUEST_MB | 110 | 整个请求体上限，包含批量PDF和multipart开销；长度已声明或分块均受限 |
| DEMO_MAX_MUTATIONS | 2 | 同时处理的HTTP写请求上限，不等于后台任务总数 |
| DEMO_MODEL_CALLS_PER_HOUR | 0 | 全工具共享、SQLite持久化的UTC整点小时模型请求尝试上限 |
| DEMO_MODEL_TOKENS_PER_HOUR | 0 | 同小时输出预留token上限，检查先于服务商请求；不是实际计费token |
| MAX_UPLOAD_MB / MAX_PAGES | 100 / 1500 | 每PDF限制；不能覆盖总请求体限制 |

表中环境变量都带`SCHOLARTRACE_`前缀，精确模板见`.env.demo.example`。HTTP或模型额度达到上限会拒绝或降级；服务商失败也消耗已预留请求，防止重试绕过限制。模型额度不包括输入token或服务商其他收费项。按工作区计数，改数据目录会建立另一个计数空间；不是跨机器账户总账。

只读仍会执行建表、索引中断标记等启动维护，不是SQLite物理只读。交互模式仍有后台队列及磁盘增长；当前没有完整磁盘/后台任务配额或自动清理。演示库应只放可共享素材，单实例运行并监控磁盘，不能宣称多租户生产就绪。

## 公网部署入口

需要一台可持续运行本项目的服务器、域名/DNS及HTTPS入口。当前实测为Windows + Python3.12；Docker和Linux锁文件安装尚未实测。不要把本机开发库直接同步给所有体验者。

`serve_demo.py`固定单进程监听127.0.0.1，忽略转发头，并用工作区文件锁防止第二个同类启动器并行接管。将独立演示数据目录持久保存；只让同机反向代理对外提供服务。不要改用裸`uvicorn --host 0.0.0.0`绕过该入口。

在私有`.env.demo`中把`SCHOLARTRACE_DEMO_ORIGIN`改成实际`https://域名`，与浏览器Origin和代理保留的Host一致。配置不完整或公网地址使用普通HTTP时启动拒绝。HTTP Basic本身不加密凭据，公网必须由TLS保护，依据[RFC 7617](https://www.rfc-editor.org/rfc/rfc7617)。

`deploy/Caddyfile.example`是待替换实际域名的模板，转发至127.0.0.1:8000，保留Host并设置请求体上限及流式响应。语法依据[Caddy反向代理](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)和[请求体限制](https://caddyserver.com/docs/caddyfile/directives/request_body)官方文档；本机没有Caddy，尚未安装、启动或实测TLS/证书。上服务器后先`caddy validate --config 实际配置路径 --adapter caddyfile`，再按实际运行账户、域名和网络配置部署。

不配置宽泛代理信任。当前直接对端统计可能把所有反代访问归入同一窗口；如以后需要真实客户端IP，应只信任明确代理并另做测试。[Uvicorn设置说明](https://uvicorn.dev/settings/)描述了代理头及信任参数。

## 上线验收与备份

本阶段没有创建公网地址、远程账号或发布仓库。正式开放前应从外网验证未登录页面/API/PDF均401、正确登录后PDF可翻页、跨站写入403、只读写入403、超大请求413、限额429、模型额度降级；确认体验账号权限及服务有效期，并再次检查HTTPS证书。`/healthz`仅返回status，不暴露模型与文献；其他健康详情需登录。

HTTP Basic通常由浏览器缓存账号，当前没有可靠的应用内退出登录按钮。换账号使用新的私密会话；撤销账号需本机更换专用密码哈希并重启，不要把它当作完整会话系统。

备份前停止演示服务，确认没有另一个进程使用该目录，再把整个独立演示数据目录复制到私有备份位置；只备SQLite主文件而遗漏WAL或上传/产物文件会不完整。当前只实测同目录重启持久化，完整备份恢复和异机迁移尚待演练，不要直接改数据库绝对路径来冒充迁移已验证。
