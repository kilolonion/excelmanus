/** Guide navigation only. Exercises live in TourPractice and never mutate work. */
import { useUIStore } from "@/stores/ui-store";
import { requestModelSubTab } from "@/components/settings/model/model-subtab";

export function runEffect(key: string | undefined): void {
  const ui = useUIStore.getState();
  switch (key) {
    case "openSidebar_chats":
    case "openSidebar_files":
      ui.closeSettings();
      ui.setSidebarTab(key === "openSidebar_files" ? "files" : "chats");
      ui.setSidebarOpen(true);
      break;
    case "showComposer":
      ui.closeSettings();
      if (window.innerWidth < 1024) ui.setSidebarOpen(false);
      break;
    case "openSettings_model":
    case "openSettings_model_roles":
    case "openSettings_subscription":
      requestModelSubTab(key === "openSettings_model_roles" ? "roles" : key === "openSettings_subscription" ? "subscription" : "providers");
      ui.openSettings("model");
      break;
    case "openSettings_rules": ui.openSettings("rules"); break;
    case "openSettings_skills": ui.openSettings("skills"); break;
    case "openSettings_mcp": ui.openSettings("mcp"); break;
    case "openSettings_memory": ui.openSettings("memory"); break;
    case "openSettings_runtime": ui.openSettings("runtime"); break;
    case "openSettings_version": ui.openSettings("version"); break;
  }
}
