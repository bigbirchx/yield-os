import { Suspense } from "react";
import OptimizerClient from "./OptimizerClient";

export const metadata = {
  title: "Route Optimizer | Yield Cockpit",
};

export default function OptimizerPage() {
  return (
    <Suspense>
      <OptimizerClient />
    </Suspense>
  );
}
