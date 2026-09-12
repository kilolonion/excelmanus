import { useUIStore } from "@/stores/ui-store";
import type { ModelInfo } from "@/lib/types";

/** 仅在明确知道视觉能力时写入 store；未知时不要当成不支持。 */
export function applyKnownVisionCapability(value: boolean | null | undefined): void {
  if (typeof value === "boolean") {
    useUIStore.getState().setVisionCapable(value);
  }
}

export function applyVisionFromModel(model: ModelInfo | undefined): void {
  applyKnownVisionCapability(model?.supports_vision);
}
