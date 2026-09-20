"use client";

import dynamic from "next/dynamic";
import { useFilePreviewStore } from "@/stores/file-preview-store";

const CodePreviewModal = dynamic(() => import("@/components/chat/CodePreviewModal").then((m) => m.CodePreviewModal), { ssr: false });
const ImagePreviewModal = dynamic(() => import("@/components/chat/ImagePreviewModal").then((m) => m.ImagePreviewModal), { ssr: false });

export function FilePreviewHost() {
  const textTarget = useFilePreviewStore((s) => s.textTarget);
  const textOpen = useFilePreviewStore((s) => s.textOpen);
  const imageTarget = useFilePreviewStore((s) => s.imageTarget);
  const imageOpen = useFilePreviewStore((s) => s.imageOpen);
  const closeText = useFilePreviewStore((s) => s.closeText);
  const closeImage = useFilePreviewStore((s) => s.closeImage);

  return (
    <>
      {textTarget && (
        <CodePreviewModal
          filePath={textTarget.path}
          filename={textTarget.filename}
          sessionId={textTarget.sessionId}
          workspaceId={textTarget.workspaceId}
          open={textOpen}
          onOpenChange={(open) => {
            if (!open) closeText();
          }}
        />
      )}
      {imageTarget && (
        <ImagePreviewModal
          imagePath={imageTarget.path}
          filename={imageTarget.filename}
          sessionId={imageTarget.sessionId}
          workspaceId={imageTarget.workspaceId}
          open={imageOpen}
          onOpenChange={(open) => {
            if (!open) closeImage();
          }}
        />
      )}
    </>
  );
}
