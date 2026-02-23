declare module "*.css" {
  const content: Record<string, string>;
  export default content;
}

declare module "@univerjs/preset-sheets-core/lib/index.css";
declare module "@univerjs/preset-sheets-advanced/lib/index.css";
