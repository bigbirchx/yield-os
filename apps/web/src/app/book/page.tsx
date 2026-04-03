import { Suspense } from "react";
import BookClient from "./BookClient";

export const metadata = {
  title: "Book Analysis | Yield Cockpit",
};

export default function BookPage() {
  return (
    <Suspense>
      <BookClient />
    </Suspense>
  );
}
