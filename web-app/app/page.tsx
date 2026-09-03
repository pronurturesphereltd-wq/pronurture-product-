import { redirect } from "next/navigation";

export default function Home() {
  // The session lives in the browser, so the signed-in check happens on
  // /login and /rota. Everything enters through /login.
  redirect("/login");
}
