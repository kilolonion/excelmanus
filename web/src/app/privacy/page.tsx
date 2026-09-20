"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";

export default function PrivacyPage() {
  return (
    <div className="min-h-screen bg-gradient-to-b from-background to-muted/30">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 py-8 sm:py-12">
        {/* Header */}
        <div className="mb-8">
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors mb-6"
          >
            <ArrowLeft className="h-4 w-4" />
            返回首页
          </Link>
          <h1 className="text-3xl font-bold tracking-tight">隐私政策</h1>
          <p className="text-muted-foreground text-sm mt-2">版本 v1.0.0 · 最近更新日期：2026 年 9 月 19 日</p>
        </div>

        {/* Content */}
        <article className="space-y-6 text-sm sm:text-base leading-relaxed text-muted-foreground [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-foreground [&_h2]:mt-8 [&_h2]:mb-4 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:text-foreground [&_h3]:mt-6 [&_h3]:mb-3 [&_strong]:text-foreground [&_ol]:list-decimal [&_ol]:pl-6 [&_ol]:space-y-2 [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:space-y-2 [&_li]:pl-1">
          <p>ExcelManus（以下简称“本产品”）由个人开发者 kilolonion（以下简称“我们”）基于开源社区协作开发并运营。我们重视您的个人信息与隐私，并将遵守中华人民共和国相关法律法规。请您在使用本产品前仔细阅读本隐私政策。</p>
          <hr className="border-border" />
          <h2>一、我们收集的信息</h2>
          <h3>1.1 您主动提供的信息</h3>
          <ul>
          <li><strong>上传的文件</strong>：您上传的 Excel、Word、CSV、图片等文件，以及您授权本产品访问的工作区文件和相关数据。
          </li><li><strong>模型凭证</strong>：您在设置中配置的 API Key，或连接 ChatGPT/Codex 订阅时产生的 OAuth 令牌。这些凭证加密保存在部署实例的主数据库中，加密密钥保存在对应的数据目录。
          </li></ul>
          <h3>1.2 自动收集的信息</h3>
          <ul>
          <li><strong>对话与任务记录</strong>：您与 AI 助手的对话、附件引用、工具结果、审批回答及任务状态，用于上下文、历史记录和中断恢复。
          </li><li><strong>操作与诊断记录</strong>：工具调用、文件修改、运行错误和请求指标，用于诊断、版本管理及恢复。独立服务日志和评测记录可能包含任务内容。
          </li><li><strong>界面状态</strong>：浏览器中的会话缓存、界面偏好和引导进度；桌面版也会在本机保留相应界面数据。
          </li></ul>
          <h3>1.3 信息访问边界</h3>
          <ul>
          <li>我们<strong>不会</strong>收集与本产品功能无关的个人信息。
          </li><li>本产品可根据任务读取您授权的工作区文件，包括未通过上传入口添加的文件；文件访问受当前工作区与权限规则限制。
          </li><li>自行部署不会自动向项目维护者开放数据访问权限；您主动提供的反馈、日志或问题附件除外。
          </li></ul>
          <h2>二、信息的使用</h2>
          <p>我们收集的信息仅用于以下目的：</p>
          <ol>
          <li><strong>提供核心服务</strong>：处理您的表格与文档、执行数据分析、生成图表等。
          </li><li><strong>会话管理</strong>：保存对话历史、支持会话恢复和上下文延续。
          </li><li><strong>安全保障</strong>：操作审计、异常行为检测、代码执行安全审查。
          </li><li><strong>问题诊断</strong>：使用本地运行记录排查故障；您可自行决定是否向维护者提供相关反馈。
          </li></ol>
          <h2>三、信息的存储</h2>
          <ol>
          <li><strong>本机或自行部署</strong>：主数据库、凭证、工作区文件和日志保存在运行实例的机器上。源码版默认 data home 为 <code>~/.excelmanus</code>，桌面版默认使用应用用户数据目录下的 <code>profile/</code>；另行登记的工作区保留在原位置。模型或外部工具调用仍可能向所配置的第三方发送相关内容。
          </li><li><strong>在线服务</strong>（如有）：实际存储位置、运营方及其数据处理方式，以该服务公开说明为准；开源源码本身不限定服务器所在地区。
          </li><li><strong>存储期限</strong>：
          <ul>
          <li><strong>对话记录</strong>：可通过界面删除或清空，具体保存还受聊天记录设置影响。
          </li><li><strong>工作区文件、修订、记忆和独立日志</strong>：按各自的存储与清理规则保留。删除会话不等于删除已生成文件、工作区 <code>.excelmanus/revisions/</code>、记忆、服务日志或备份。
          </li><li><strong>浏览器与桌面缓存</strong>：可通过清理对应浏览器或应用数据移除，不会因此自动删除后端数据库和工作区文件。
          </li></ul>
          </li></ol>
          <h2>四、信息的共享与披露</h2>
          <p>我们<strong>不会</strong>主动向第三方出售、出租或共享您的个人信息，但以下情形除外：</p>
          <ol>
          <li><strong>获得您的明确同意</strong>。
          </li><li><strong>第三方 API 调用</strong>：使用大语言模型（LLM）功能时，您的对话、工具返回的文件内容及图片可能发送至您配置的模型服务商。启用搜索、MCP 或 Jev 时，对应查询、参数或评估上下文也可能发送至相关服务。跨境传输取决于您选择的服务及其处理位置；请了解对应服务商的隐私说明。
          </li><li><strong>法律法规要求</strong>：根据适用的法律法规、法律程序或政府主管部门的强制性要求。
          </li><li><strong>保护权益</strong>：在紧急情况下为保护我们、用户或公众的人身财产安全。
          </li></ol>
          <h2>五、信息安全</h2>
          <p>我们采取以下措施保护您的信息安全：</p>
          <ol>
          <li><strong>传输保护</strong>：服务器部署可通过 HTTPS 反向代理保护访问；本机服务默认使用 loopback HTTP。是否启用 HTTPS 取决于部署配置。
          </li><li><strong>路径沙盒</strong>：文件访问限制在工作区目录内，防止路径穿越。
          </li><li><strong>代码执行约束</strong>：代码策略、路径检查和超时限制用于约束本机代码执行；这些措施不是操作系统级隔离。
          </li><li><strong>操作审批</strong>：按工具风险与当前授权决定是否需要确认；普通工作区编辑不一定逐次弹窗。
          </li><li><strong>工作区边界</strong>：会话绑定各自的已登记文件夹；同一实例仍共享模型凭证、记忆和外部服务配置，不提供多租户隔离。
          </li></ol>
          <p>尽管我们尽力保护您的信息安全，但受限于技术水平，无法保证信息百分之百安全。如发生安全事件，我们将及时通知受影响的用户。</p>
          <h2>六、您的权利及行使方式</h2>
          <p>您对个人信息享有以下权利：</p>
          <ol>
          <li><strong>查阅与导出</strong>：您可以查看会话与文件，并通过设置页导出配置。配置导出不等于完整的数据备份。
          </li><li><strong>删除</strong>：您可以删除会话、管理工作区文件，并按需清理记忆、日志、修订与备份。
          </li><li><strong>撤回同意</strong>：您可以在设置中关闭特定功能（如对话历史记录）。
          </li><li><strong>投诉与反馈</strong>：如您认为我们的个人信息处理侵害了您的权益，可发邮件至 kilolonion@gmail.com，或向网信部门投诉举报。
          </li></ol>
          <p>我们将在收到请求后尽快处理并反馈。开源项目的维护资源有限，具体响应时间可能有所不同。</p>
          <h2>七、未成年人保护</h2>
          <p>本产品主要面向成年用户。如您为未满 14 周岁的未成年人，请在监护人的指导和同意下使用本产品。我们不会故意收集未成年人的个人信息。如发现误收集，我们将及时删除。</p>
          <h2>八、Cookie 与本地存储</h2>
          <ol>
          <li>本产品使用 localStorage / IndexedDB 保存会话缓存、界面偏好与引导状态，使用 sessionStorage 保存当前浏览器会话中的管理令牌。
          </li><li>本产品不提供产品级账号登录。第三方订阅 OAuth 由相应服务商完成，授权凭证保存在本实例的后端。
          </li><li>清理浏览器数据可移除浏览器侧记录，但不会撤销服务商授权，也不会清除后端凭证、文件或备份。
          </li></ol>
          <h2>九、政策变更</h2>
          <p>我们可能会不时修订本隐私政策。修订后的政策将在本页面公布，重大变更将通过站内通知或邮件等方式提前告知。继续使用本产品即视为同意变更后的政策。</p>
          <h2>十、联系我们</h2>
          <p>如您对本隐私政策有任何疑问、意见或建议，可通过以下方式联系我们：</p>
          <ul>
          <li><strong>邮箱</strong>：kilolonion@gmail.com
          </li><li><strong>Gitee Issues</strong>：<a href="https://gitee.com/kilolonion/excelmanus/issues" target="_blank" rel="noopener noreferrer" className="text-[var(--em-primary)] hover:underline">https://gitee.com/kilolonion/excelmanus/issues</a>
          </li><li><strong>GitHub Issues</strong>：<a href="https://github.com/kilolonion/excelmanus/issues" target="_blank" rel="noopener noreferrer" className="text-[var(--em-primary)] hover:underline">https://github.com/kilolonion/excelmanus/issues</a>
          </li></ul>
          <hr className="border-border" />
          <p><strong>kilolonion</strong></p>
          <p><strong>2026 年 9 月 19 日</strong></p>
        </article>

        {/* Footer nav */}
        <div className="mt-12 pt-6 border-t border-border flex items-center justify-between text-sm text-muted-foreground">
          <Link href="/terms" className="hover:text-foreground transition-colors">
            ← 用户服务协议
          </Link>
          <Link href="/" className="hover:text-foreground transition-colors">
            返回首页
          </Link>
        </div>
      </div>
    </div>
  );
}
