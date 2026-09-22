import { getUniverModules as actual } from "../../lib/univer-modules";

export async function getUniverModules() {
  const modules = await actual();
  return { ...modules, createUniver: (...args: Parameters<typeof modules.createUniver>) => {
    const result = modules.createUniver(...args);
    const api = result.univerAPI;
    const testWindow = window as typeof window & { multiWorkbookAPIs: typeof api[] };
    testWindow.multiWorkbookAPIs ??= [];
    testWindow.multiWorkbookAPIs.push(api);
    return result;
  } };
}
export { prefetchUniverModules, warmUniverModules } from "../../lib/univer-modules";
