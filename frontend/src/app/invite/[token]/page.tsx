"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { SiteShell } from "@/components/site/site-chrome";
import { Button, Field, Input } from "@/components/ui/primitives";
import { ApiError, get, post } from "@/lib/api";
import { useL } from "@/lib/i18n";

type Info = { email: string; role: string; org_name: string | null; existing_account: boolean };

export default function InvitePage() {
  const { token } = useParams<{ token: string }>();
  const L = useL();
  const [name, setName] = useState("");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const info = useQuery({ queryKey: ["invite", token], queryFn: () => get<Info>(`/auth/invitations/${token}`), retry: false });
  const accept = useMutation({ mutationFn: () => post("/auth/invitations/accept", { token, full_name: name || info.data?.email, password: pw || "existing-account-placeholder" }) });
  const i = info.data;
  const ok = i?.existing_account || (name.trim().length >= 2 && pw.length >= 12 && pw === pw2);
  return (
    <SiteShell>
      <section className="mx-auto max-w-md px-4 pt-16 sm:px-6">
        <h1 className="font-serif text-3xl">{L("Invitation", "Invitation")}</h1>
        {info.isLoading ? <p className="mt-4 text-ink-3">{L("Vérification du lien…", "Checking the link…")}</p> : info.error ? (
          <p className="mt-4 text-risk-high" role="alert">{L("Ce lien d'invitation est invalide, expiré ou déjà utilisé. Demandez-en un nouveau à votre administrateur.", "This invitation link is invalid, expired or already used. Ask your administrator for a new one.")}</p>
        ) : accept.isSuccess ? (
          <div className="mt-4"><p>{L("Votre accès est activé.", "Your access is active.")}</p><Link href="/login" className="mt-4 inline-block text-accent hover:underline">{L("Se connecter", "Sign in")}</Link></div>
        ) : i && (
          <form className="mt-6 flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (ok) accept.mutate(); }}>
            <p className="text-ink-2">{L(`Vous êtes invité à rejoindre ${i.org_name ?? "une organisation"} avec le rôle ${i.role}.`, `You are invited to join ${i.org_name ?? "an organisation"} with the role ${i.role}.`)}</p>
            <p className="text-sm text-ink-3">{i.email}</p>
            {!i.existing_account && <>
              <Field label={L("Nom complet", "Full name")} htmlFor="n"><Input id="n" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} /></Field>
              <Field label={L("Mot de passe (12 caractères minimum)", "Password (at least 12 characters)")} htmlFor="p"><Input id="p" type="password" autoComplete="new-password" value={pw} onChange={(e) => setPw(e.target.value)} /></Field>
              <Field label={L("Confirmation", "Confirmation")} htmlFor="p2" error={pw2 && pw2 !== pw ? L("Les mots de passe diffèrent", "Passwords differ") : undefined}><Input id="p2" type="password" autoComplete="new-password" value={pw2} onChange={(e) => setPw2(e.target.value)} /></Field>
            </>}
            {i.existing_account && <p className="text-sm text-ink-2">{L("Un compte existe déjà pour cette adresse : l'organisation sera ajoutée à vos accès.", "An account already exists for this address: the organisation will be added to your access.")}</p>}
            {accept.error && <p className="text-sm text-risk-high" role="alert">{accept.error instanceof ApiError ? accept.error.message : String(accept.error)}</p>}
            <Button type="submit" variant="primary" disabled={!ok} loading={accept.isPending}>{L("Accepter l'invitation", "Accept the invitation")}</Button>
          </form>
        )}
      </section>
    </SiteShell>
  );
}
