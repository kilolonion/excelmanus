"use client";

import { Check } from "lucide-react";

interface MiniCheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  className?: string;
}

export function MiniCheckbox({ checked, onChange, label, className = "" }: MiniCheckboxProps) {
  return (
    <label
      className={`flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none ${className}`}
      onClick={(e) => { e.preventDefault(); onChange(!checked); }}
    >
      <span
        role="checkbox"
        aria-checked={checked}
        className={[
          "inline-flex items-center justify-center shrink-0 rounded-[3px] border transition-colors",
          "h-[14px] w-[14px]",
          checked
            ? "bg-primary border-primary text-primary-foreground"
            : "border-muted-foreground/40 bg-transparent",
        ].join(" ")}
      >
        {checked && <Check className="h-[10px] w-[10px]" strokeWidth={2.5} />}
      </span>
      {label}
    </label>
  );
}
