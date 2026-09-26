"use client";

import { Checkbox } from "./checkbox";

interface MiniCheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  className?: string;
  disabled?: boolean;
}

export function MiniCheckbox({ checked, onChange, label, className = "", disabled }: MiniCheckboxProps) {
  return (
    <label
      className={`flex items-center gap-1.5 text-xs text-muted-foreground select-none ${disabled ? "cursor-not-allowed opacity-50" : "cursor-pointer"} ${className}`}
    >
      <Checkbox
        checked={checked}
        disabled={disabled}
        onCheckedChange={(next) => onChange(next === true)}
        aria-label={label}
      />
      {label}
    </label>
  );
}
