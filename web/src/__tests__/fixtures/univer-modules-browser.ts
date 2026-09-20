import { getUniverModules as actual } from "../../lib/univer-modules";

// Instrument the real facade, keeping the production component and engine.
export async function getUniverModules() {
  const modules = await actual();
  return { ...modules, createUniver: (...args: Parameters<typeof modules.createUniver>) => {
    const result = modules.createUniver(...args);
    const api = result.univerAPI;
    const counters = { created: 0, disposed: 0 };
    const createWorkbook = api.createWorkbook.bind(api);
    api.createWorkbook = (...params) => { counters.created++; return createWorkbook(...params); };
    const disposeUnit = api.disposeUnit.bind(api);
    api.disposeUnit = (...params) => { counters.disposed++; return disposeUnit(...params); };
    Object.assign(window, { workbookAPI: api, workbookCounters: counters });
    return result;
  } };
}
export { prefetchUniverModules, warmUniverModules } from "../../lib/univer-modules";
