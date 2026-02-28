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
            href="/register"
            className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground transition-colors mb-6"
          >
            <ArrowLeft className="h-4 w-4" />
            返回注册
          </Link>
          <h1 className="text-3xl font-bold tracking-tight">隐私政策</h1>
          <p className="text-muted-foreground text-sm mt-2">版本 v1.0.0 · 最近更新日期：2026 年 2 月 28 日</p>
        </div>

        {/* Content */}
        <article className="space-y-6 text-sm sm:text-base leading-relaxed text-muted-foreground [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-foreground [&_h2]:mt-8 [&_h2]:mb-4 [&_h3]:text-base [&_h3]:font-semibold [&_h3]:text-foreground [&_h3]:mt-6 [&_h3]:mb-3 [&_strong]:text-foreground [&_ol]:list-decimal [&_ol]:pl-6 [&_ol]:space-y-2 [&_ul]:list-disc [&_ul]:pl-6 [&_ul]:space-y-2 [&_li]:pl-1">
          <p>
            ExcelManus（以下简称「本产品」）由个人开发者 kilolonion（以下简称「我们」）基于开源社区协作开发并运营。
            我们深知个人信息对您的重要性，将遵守中华人民共和国相关法律法规，保护您的个人信息及隐私安全。
            请您在使用本产品前仔细阅读本隐私政策。
          </p>

          <hr className="border-border" />

          <h2>一、我们收集的信息</h2>

          <h3>1.1 您主动提供的信息</h3>
          <ul>
            <li><strong>账号信息</strong>：注册时提供的邮箱地址、昵称、密码（加密存储）。</li>
            <li><strong>第三方登录信息</strong>：通过 GitHub、Google、QQ 等方式登录时，我们会获取对应平台授权的基本信息（如用户名、头像、邮箱）。</li>
            <li><strong>上传的文件</strong>：您主动上传至本产品的 Excel 文件及相关数据。</li>
          </ul>

          <h3>1.2 自动收集的信息</h3>
          <ul>
            <li><strong>对话记录</strong>：您与 AI 助手的对话内容，用于维持会话上下文和历史记录功能。</li>
            <li><strong>操作日志</strong>：工具调用记录、文件修改审计日志，用于版本管理和回滚功能。</li>
            <li><strong>基础设备信息</strong>：浏览器类型、操作系统类型等，用于适配界面显示，不会持久化存储。</li>
          </ul>

          <h3>1.3 我们不会收集的信息</h3>
          <ul>
            <li>我们<strong>不会</strong>收集与本产品功能无关的个人信息。</li>
            <li>我们<strong>不会</strong>通过本产品读取您设备上未主动上传的文件。</li>
            <li>在自行部署场景下，我们<strong>无法</strong>接触到您的任何数据。</li>
          </ul>

          <h2>二、信息的使用</h2>
          <p>我们收集的信息仅用于以下目的：</p>
          <ol>
            <li><strong>提供核心服务</strong>：处理您的 Excel 文件、执行数据分析、生成图表等。</li>
            <li><strong>账号管理</strong>：验证身份、维护账号安全。</li>
            <li><strong>会话管理</strong>：保存对话历史、支持会话恢复和上下文延续。</li>
            <li><strong>安全保障</strong>：操作审计、异常行为检测、代码执行安全审查。</li>
            <li><strong>产品改进</strong>：匿名化的使用统计（如功能使用频率），用于优化用户体验。</li>
          </ol>

          <h2>三、信息的存储</h2>
          <ol>
            <li><strong>自行部署</strong>：所有数据存储在您自己的服务器上，包括 SQLite 数据库、工作区文件和配置信息。我们无法也不会访问。</li>
            <li><strong>在线服务</strong>（如有）：数据存储于中华人民共和国境内的服务器。我们采取合理的技术和管理措施保护数据安全。</li>
            <li><strong>存储期限</strong>：账号信息（邮箱、昵称、密码哈希）在账号存续期间保存，注销后 30 日内删除；对话记录和操作日志随会话生命周期保存，您可随时手动删除；浏览器/设备信息不持久化存储。</li>
            <li><strong>密码安全</strong>：用户密码经不可逆哈希处理后存储，我们无法获知您的明文密码。</li>
          </ol>

          <h2>四、信息的共享与披露</h2>
          <p>我们<strong>不会</strong>主动向第三方出售、出租或共享您的个人信息，但以下情形除外：</p>
          <ol>
            <li><strong>获得您的明确同意</strong>。</li>
            <li><strong>第三方 API 调用</strong>：使用大语言模型（LLM）功能时，您的对话内容会发送至您配置的 API 服务商（如 OpenAI、Anthropic、Google 等）。如您配置的是境外服务商，对话数据将被传输至境外服务器——这是由您自主选择并配置的，请您自行了解对应服务商的隐私政策并评估风险。</li>
            <li><strong>法律法规要求</strong>：根据适用的法律法规、法律程序或政府主管部门的强制性要求。</li>
            <li><strong>保护权益</strong>：在紧急情况下为保护我们、用户或公众的人身财产安全。</li>
          </ol>

          <h2>五、信息安全</h2>
          <p>我们采取以下措施保护您的信息安全：</p>
          <ol>
            <li><strong>传输加密</strong>：支持 HTTPS 加密传输。</li>
            <li><strong>路径沙盒</strong>：文件访问限制在工作区目录内，防止路径穿越。</li>
            <li><strong>代码审查</strong>：用户代码执行前经过静态安全分析。</li>
            <li><strong>操作审批</strong>：高风险操作需用户确认后方可执行。</li>
            <li><strong>用户隔离</strong>：多用户模式下，各用户数据物理隔离。</li>
            <li><strong>密码保护</strong>：密码经加盐哈希处理，不可逆存储。</li>
          </ol>
          <p>
            尽管我们尽力保护您的信息安全，但受限于技术水平，无法保证信息百分之百安全。如发生安全事件，我们将及时通知受影响的用户。
          </p>

          <h2>六、您的权利及行使方式</h2>
          <p>您对个人信息享有以下权利：</p>
          <ol>
            <li><strong>查阅与导出</strong>：您可以通过 Web UI 个人资料页或 CLI <code>/config export</code> 命令查看和导出您的数据。</li>
            <li><strong>更正</strong>：您可以在个人资料页面修改昵称、密码等信息。</li>
            <li><strong>删除</strong>：您可以通过侧边栏删除会话记录，或使用 CLI <code>/clear</code> 命令清除对话历史。</li>
            <li><strong>注销账号</strong>：您可以在个人资料页点击「注销账号」，或发邮件至 kilolonion@gmail.com 申请注销。注销后 30 日内我们将删除您的个人信息。</li>
            <li><strong>撤回同意</strong>：您可以在设置中关闭特定功能（如对话历史记录），或注销账号以全面撤回同意。</li>
            <li><strong>投诉与反馈</strong>：如您认为我们的个人信息处理侵害了您的权益，可发邮件至 kilolonion@gmail.com，或向网信部门投诉举报。</li>
          </ol>
          <p>我们将在收到您的请求后尽快处理并反馈。作为开源小项目，我们的响应速度可能不及商业服务，但我们会认真对待每一条请求。</p>

          <h2>七、未成年人保护</h2>
          <p>
            本产品主要面向成年用户。如您为未满 14 周岁的未成年人，请在监护人的指导和同意下使用本产品。
            我们不会故意收集未成年人的个人信息。如发现误收集，我们将及时删除。
          </p>

          <h2>八、Cookie 与本地存储</h2>
          <ol>
            <li>本产品使用浏览器本地存储（localStorage / IndexedDB）保存会话状态和用户偏好设置。</li>
            <li>使用 JWT Token 维持登录状态。</li>
            <li>上述数据存储在您的浏览器中，清除浏览器数据即可移除。</li>
          </ol>

          <h2>九、政策变更</h2>
          <p>
            我们可能会不时修订本隐私政策。修订后的政策将在本页面公布，重大变更将通过站内通知或邮件等方式提前告知。
            继续使用本产品即视为同意变更后的政策。
          </p>

          <h2>十、联系我们</h2>
          <p>如您对本隐私政策有任何疑问、意见或建议，可通过以下方式联系我们：</p>
          <ul>
            <li><strong>邮箱</strong>：kilolonion@gmail.com</li>
            <li>
              <strong>GitHub Issues</strong>：
              <a href="https://github.com/kilolonion/excelmanus/issues" target="_blank" rel="noopener noreferrer" className="text-[var(--em-primary)] hover:underline">
                https://github.com/kilolonion/excelmanus/issues
              </a>
            </li>
          </ul>

          <hr className="border-border" />

          <p className="text-center">
            <strong>kilolonion</strong><br />
            2026 年 2 月 28 日
          </p>
        </article>

        {/* Footer nav */}
        <div className="mt-12 pt-6 border-t border-border flex items-center justify-between text-sm text-muted-foreground">
          <Link href="/terms" className="hover:text-foreground transition-colors">
            ← 用户服务协议
          </Link>
          <Link href="/register" className="hover:text-foreground transition-colors">
            返回注册
          </Link>
        </div>
      </div>
    </div>
  );
}
