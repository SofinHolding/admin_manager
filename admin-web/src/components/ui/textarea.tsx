import * as React from "react";
import { cn } from "../../lib/utils";

export const Textarea = React.forwardRef<HTMLTextAreaElement, React.TextareaHTMLAttributes<HTMLTextAreaElement>>(
  ({ className, ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        "flex min-h-[6rem] w-full rounded-md border border-border-strong bg-elev-1 px-3 py-2 text-sm transition-colors duration-150",
        "hover:border-primary/35",
        "focus-visible:border-primary focus-visible:bg-surface-1 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-45",
        "placeholder:text-muted-foreground",
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = "Textarea";
