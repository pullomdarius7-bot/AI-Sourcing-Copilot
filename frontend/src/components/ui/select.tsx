import * as React from "react";
import { cn } from "@/lib/utils";

/** Native select styled to match the inputs (keyboard and screen-reader friendly by default). */
const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, ...props }, ref) => (
    <select ref={ref} className={cn("h-10 w-full rounded-md border border-input bg-card px-3 text-sm", className)} {...props} />
  ),
);
Select.displayName = "Select";

export { Select };
