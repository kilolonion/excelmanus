/**
 * 服务端组件：将运行时环境变量注入到客户端 window.__EXCELMANUS_RUNTIME__。
 *
 * 在 Server Component 中，非 NEXT_PUBLIC_ 前缀的 process.env.* 是运行时读取的，
 * 不会被 next build 内联。这使得同一个 Docker 镜像可以通过环境变量在运行时
 * 切换后端地址，解决了 NEXT_PUBLIC_BACKEND_ORIGIN 构建时固化的问题。
 *
 * 环境变量映射：
 *   EXCELMANUS_RUNTIME_BACKEND_ORIGIN → backendOrigin
 *   EXCELMANUS_RUNTIME_AUTH_ENABLED   → authEnabled
 *
 * 如果运行时变量未设置，对应字段不会注入，客户端会回退到构建时的 NEXT_PUBLIC_* 值。
 */
export function RuntimeConfigScript() {
  // 在 Server Component 中读取运行时环境变量
  const backendOrigin = process.env.EXCELMANUS_RUNTIME_BACKEND_ORIGIN?.trim() || "";
  const authEnabled = process.env.EXCELMANUS_RUNTIME_AUTH_ENABLED?.trim() || "";

  // 仅注入有值的字段
  const config: Record<string, string> = {};
  if (backendOrigin) config.backendOrigin = backendOrigin;
  if (authEnabled) config.authEnabled = authEnabled;

  // 没有运行时配置时不注入 script 标签
  if (Object.keys(config).length === 0) return null;

  const scriptContent = `window.__EXCELMANUS_RUNTIME__=${JSON.stringify(config)};`;

  return (
    <script
      dangerouslySetInnerHTML={{ __html: scriptContent }}
    />
  );
}
