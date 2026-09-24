import { ArrowUpRight, Github, Globe, Smartphone } from "lucide-react";
import { ANDROID_DOWNLOAD_PAGE_URL } from "@/lib/product-links";
import { SettingsCardGrid } from "./SettingsPageLayout";

const links = [
  {
    title: "GitHub",
    description: "查看源码、发行版本与问题反馈",
    url: "https://github.com/kilolonion/excelmanus",
    domain: "kilolonion/excelmanus",
    icon: Github,
  },
  {
    title: "项目主页",
    description: "了解 ExcelManus 的功能与使用方式",
    url: "https://excelmanus.com",
    domain: "excelmanus.com",
    icon: Globe,
  },
  {
    title: "Android 手机端",
    description: "下载 APK，扫码连接电脑继续工作",
    url: ANDROID_DOWNLOAD_PAGE_URL,
    domain: "GitHub Releases · APK",
    icon: Smartphone,
  },
];

export function ProjectLinks() {
  return (
    <SettingsCardGrid label="项目与下载" minCardWidth={240}>
      {links.map(({ title, description, url, domain, icon: Icon }) => (
        <a
          key={url}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="group flex min-w-0 self-stretch items-start gap-3 rounded-lg border border-border bg-muted/20 p-4 transition-colors hover:border-[var(--em-primary)] hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--em-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-[var(--em-primary-alpha-10)] text-[var(--em-primary)]">
            <Icon className="size-4" aria-hidden="true" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="flex items-center justify-between gap-2 text-sm font-semibold">
              {title}
              <ArrowUpRight className="size-4 shrink-0 text-muted-foreground transition-colors group-hover:text-[var(--em-primary)]" aria-hidden="true" />
            </span>
            <span className="mt-1 block text-xs leading-relaxed text-muted-foreground">{description}</span>
            <span className="mt-2 block break-words text-[11px] text-muted-foreground">{domain}</span>
            <span className="sr-only">（在新窗口打开）</span>
          </span>
        </a>
      ))}
    </SettingsCardGrid>
  );
}
