"use client";

import { createContext, useContext } from "react";
import type { Me } from "@/lib/types";

const MeContext = createContext<Me | null>(null);

export const MeProvider = MeContext.Provider;

export function useMe(): Me {
  const me = useContext(MeContext);
  if (!me) throw new Error("useMe must be used inside the app shell");
  return me;
}
