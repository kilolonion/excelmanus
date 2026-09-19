"use client";

import { CodePreviewModal } from "@/components/chat/CodePreviewModal";
import { ImagePreviewModal } from "@/components/chat/ImagePreviewModal";
import { useFilePreviewStore } from "@/stores/file-preview-store";

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
          open={imageOpen}
          onOpenChange={(open) => {
            if (!open) closeImage();
          }}
        />
      )}
    </>
  );
}
