"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";

export default function TermsPage() {
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
          <h1 className="text-3xl font-bold tracking-tight">用户服务协议</h1>
          <p className="text-muted-foreground text-sm mt-2">版本 v1.0.0 · 最近更新日期：2026 年 2 月 28 日</p>
        </div>

        {/* Content */}
        <article className="space-y-6 text-sm sm:text-base leading-relaxed text-muted-foreground [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-foreground [&_h2]:mt-8 [&_h2]:mb-4 [&_strong]:text-foreground [&_ol]:list-decimal [&_ol]:pl-6 [&_ol]:space-y-2 [&_li]:pl-1">
          <p>
            欢迎使用 ExcelManus（以下简称「本产品」）。本产品由 kilolonion（以下简称「我们」）基于 Apache License 2.0
            开源协议开发并运营。请您在使用本产品前仔细阅读本协议的全部内容。
            <strong>一旦您注册、登录或以任何方式使用本产品，即视为您已充分理解并同意本协议。</strong>
          </p>

          <hr className="border-border" />

          <h2>一、适用范围</h2>
          <ol>
            <li>本产品主要面向具有完全民事行为能力的成年用户，用于合法的 Excel 数据处理与分析。</li>
            <li>本产品<strong>不适用于</strong>需要专业资质的场景（如医疗诊断、法律咨询、金融交易决策等），AI 生成的内容仅供参考，不应作为重要决策的唯一依据。</li>
            <li>未满 18 周岁的未成年人应在监护人指导下使用本产品。</li>
          </ol>

          <h2>二、服务说明</h2>
          <ol>
            <li>ExcelManus 是一款基于大语言模型（LLM）驱动的 Excel 智能处理工具，提供自然语言驱动的 Excel 读写、数据分析、图表生成、代码执行等功能。</li>
            <li>本产品以「现状」（AS IS）提供服务，不对服务的持续性、及时性、安全性和准确性作出任何明示或暗示的保证。</li>
            <li>本产品为开源项目，用户可自行部署。我们提供的在线演示服务仅供体验和测试用途。</li>
          </ol>

          <h2>三、账号注册与管理</h2>
          <ol>
            <li>您在注册时应提供真实、准确的信息，并妥善保管账号及密码。因账号信息泄露导致的一切后果由您自行承担。</li>
            <li>每位用户仅可注册一个账号，不得通过批量注册、机器人注册等方式滥用服务资源。</li>
            <li>您有权随时注销账号。注销后，我们将在合理期限内删除您的个人信息（法律法规要求保留的除外）。</li>
            <li>我们有权对异常账号（包括但不限于长期未使用、违规使用、批量注册等）进行清理或停用。</li>
          </ol>

          <h2>四、用户行为规范</h2>
          <p>使用本产品时，您不得从事以下行为：</p>
          <ol>
            <li>上传、处理含有违反中华人民共和国法律法规的内容，包括但不限于涉及国家安全、淫秽色情、暴力恐怖、赌博诈骗等违法信息；</li>
            <li>利用本产品从事任何侵犯他人知识产权、商业秘密、个人隐私等合法权益的活动；</li>
            <li>通过本产品执行恶意代码、发起网络攻击、扫描漏洞或从事其他危害网络安全的行为；</li>
            <li>以任何方式干扰、破坏本产品的正常运行，或对服务器造成不合理的负载；</li>
            <li>利用本产品生成虚假信息、实施欺诈或误导他人；</li>
            <li>其他违反法律法规、公序良俗或本协议约定的行为。</li>
          </ol>

          <h2>五、数据与文件</h2>
          <ol>
            <li>您上传至本产品的 Excel 文件及相关数据，其所有权归您所有。</li>
            <li>我们不会主动访问、出售或向第三方提供您的用户数据，但法律法规另有规定的除外。</li>
            <li>因服务器故障、升级维护、不可抗力等原因导致数据丢失的，我们不承担赔偿责任。<strong>建议您定期备份重要数据。</strong></li>
            <li>在自行部署场景下，您的数据完全存储在您自己的服务器上，我们无法也不会接触。</li>
          </ol>

          <h2>六、大语言模型相关</h2>
          <ol>
            <li>本产品调用第三方大语言模型（如 OpenAI、Anthropic、Google 等）的 API 接口，相关调用受对应服务商的使用条款约束。</li>
            <li>AI 生成的内容（包括公式、代码、分析结论等）仅供参考，<strong>不构成专业建议</strong>。您应自行审核 AI 输出结果的准确性和适用性。</li>
            <li>使用自行配置的 API Key 产生的费用由您自行承担，与我们无关。</li>
            <li>当您配置境外 API 服务商时，您的对话输入内容将被传输至该服务商所在国家/地区的服务器。该传输是由您自主选择并配置的，请您在配置前自行评估相关风险。</li>
          </ol>

          <h2>七、代码执行</h2>
          <ol>
            <li>本产品具有代码执行功能（run_code），用于实现复杂数据处理。代码执行受沙盒安全策略保护。</li>
            <li>在启用「完全访问模式」或自行部署场景下，代码执行的安全风险由您自行评估和承担。</li>
            <li>我们不对代码执行结果的正确性或因代码执行导致的任何损失承担责任。</li>
          </ol>

          <h2>八、知识产权</h2>
          <ol>
            <li>本产品的源代码基于 Apache License 2.0 开源。您在遵守该许可证的前提下，可自由使用、修改和分发。</li>
            <li>「ExcelManus」名称及相关标识的商标权利归我们所有。未经许可，不得将其用于暗示我们对您的产品或服务的认可或背书。</li>
            <li>您通过本产品创建的文件和数据成果的知识产权归您所有。</li>
          </ol>

          <h2>九、免责声明</h2>
          <ol>
            <li>本产品基于开源社区协作开发，按「现状」提供，<strong>不提供任何形式的明示或暗示担保</strong>，包括但不限于对适销性、特定用途适用性和非侵权性的担保。</li>
            <li>对于因使用本产品导致的任何直接、间接、附带、特殊或后果性损害，我们不承担责任，即使我们已被告知此类损害的可能性。</li>
            <li>因第三方服务（包括但不限于 LLM API 提供商、云服务商）的变更、中断或终止导致的服务影响，我们不承担责任。</li>
            <li>因不可抗力（包括但不限于自然灾害、政策变化、网络故障等）导致服务中断或数据损失的，我们不承担责任。</li>
          </ol>

          <h2>十、投诉举报与违规处置</h2>
          <ol>
            <li>如您发现本产品中存在违法违规内容或行为，可通过邮箱 kilolonion@gmail.com 或 GitHub Issues 举报。</li>
            <li>我们将在收到举报后尽快核实处理，并反馈结果。</li>
            <li>对违规用户，我们将视情节轻重采取警示、限制功能、暂停服务或终止账号等措施。</li>
          </ol>

          <h2>十一、协议变更</h2>
          <ol>
            <li>我们有权根据法律法规变化或业务需要修订本协议。修订后的协议将在本页面公布。</li>
            <li>若您在协议变更后继续使用本产品，视为您同意变更后的协议内容。</li>
            <li>重大变更将通过站内通知或邮件等方式提前告知。</li>
          </ol>

          <h2>十二、其他</h2>
          <ol>
            <li>本协议的签订地为中华人民共和国。</li>
            <li>本协议适用中华人民共和国法律（不包括港澳台地区法律）。因本协议引起的争议，双方应友好协商解决；协商不成的，应提交被告住所地有管辖权的人民法院诉讼解决。</li>
            <li>本协议中的任何条款被认定为无效或不可执行的，不影响其余条款的效力。</li>
            <li>如您对本协议有任何疑问，欢迎通过邮箱 kilolonion@gmail.com 或 GitHub Issues 与我们联系。</li>
          </ol>

          <hr className="border-border" />

          <p className="text-center">
            <strong>kilolonion</strong><br />
            2026 年 2 月 28 日
          </p>
        </article>

        {/* Footer nav */}
        <div className="mt-12 pt-6 border-t border-border flex items-center justify-between text-sm text-muted-foreground">
          <Link href="/privacy" className="hover:text-foreground transition-colors">
            隐私政策 →
          </Link>
          <Link href="/register" className="hover:text-foreground transition-colors">
            返回注册
          </Link>
        </div>
      </div>
    </div>
  );
}
