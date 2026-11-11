import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const alertVariants = cva("rounded-md border px-3 py-2 text-sm", {
  variants: {
    variant: {
      error: "border-destructive/30 bg-destructive/5 text-destructive",
      success: "border-primary/30 bg-primary/5 text-primary",
      info: "border-border bg-muted text-foreground",
    },
  },
  defaultVariants: { variant: "info" },
});

export function Alert({
  className,
  variant,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & VariantProps<typeof alertVariants>) {
  return <div role={variant === "error" ? "alert" : "status"} className={cn(alertVariants({ variant }), className)} {...props} />;
}
