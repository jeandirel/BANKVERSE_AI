"""Synthetic DEMO bank generator (seeded, reproducible, entirely fictitious, every record flagged DEMO).

It produces a coherent French retail/SME bank: customers, accounts (valid but fictitious IBANs on bank
code 99999), cards, ~120 days of card transactions, transfers (salaries, rent, P2P, SME flows),
credit-card applications and customer messages. A small number of *simulated* fraud scenarios and
money-laundering typologies are injected; their ground truth is stored in ``sim_*`` columns which are
never used as model features — models trained on public datasets must find them on their own.
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import delete, insert, select

from bankverse.db.base import TenantBase
from bankverse.db.models.control import Organization
from bankverse.db.models.tenant import (
    Account,
    Card,
    CardTransaction,
    CreditApplication,
    Customer,
    CustomerMessage,
    Transfer,
)
from bankverse.db.session import TenantTarget, control_session, tenant_session
from bankverse.demo.ibans import make_foreign_iban, make_fr_iban
from bankverse.services import audit

FIRST = ["Camille", "Louis", "Emma", "Gabriel", "Léa", "Raphaël", "Chloé", "Arthur", "Manon", "Jules", "Inès", "Adam",
         "Sarah", "Hugo", "Jade", "Lucas", "Alice", "Nathan", "Lina", "Théo", "Zoé", "Tom", "Juliette", "Noah",
         "Anna", "Paul", "Louise", "Ethan", "Rose", "Mathis", "Lou", "Maël", "Ambre", "Sacha", "Mila", "Victor",
         "Nora", "Yanis", "Clara", "Mohamed", "Agathe", "Samuel", "Margaux", "Antoine", "Elsa", "Bastien",
         "Céline", "Olivier", "Nathalie", "Philippe", "Isabelle", "Pierre", "Sylvie", "Michel", "Catherine"]
LAST = ["Martin", "Bernard", "Thomas", "Petit", "Robert", "Richard", "Durand", "Dubois", "Moreau", "Laurent",
        "Simon", "Michel", "Lefebvre", "Leroy", "Roux", "David", "Bertrand", "Morel", "Fournier", "Girard",
        "Bonnet", "Dupont", "Lambert", "Fontaine", "Rousseau", "Vincent", "Muller", "Lefèvre", "Faure", "André",
        "Mercier", "Blanc", "Guérin", "Boyer", "Garnier", "Chevalier", "François", "Legrand", "Gauthier", "Garcia",
        "Perrin", "Robin", "Clément", "Morin", "Nicolas", "Henry", "Roussel", "Mathieu", "Gautier", "Masson"]
CITIES = [  # (city, postal code, region, weight)
    ("Paris", "75011", "Île-de-France", 18), ("Lyon", "69003", "Auvergne-Rhône-Alpes", 8),
    ("Marseille", "13006", "Provence-Alpes-Côte d'Azur", 7), ("Toulouse", "31000", "Occitanie", 6),
    ("Nice", "06000", "Provence-Alpes-Côte d'Azur", 4), ("Nantes", "44000", "Pays de la Loire", 5),
    ("Strasbourg", "67000", "Grand Est", 4), ("Montpellier", "34000", "Occitanie", 4),
    ("Bordeaux", "33000", "Nouvelle-Aquitaine", 5), ("Lille", "59000", "Hauts-de-France", 5),
    ("Rennes", "35000", "Bretagne", 4), ("Grenoble", "38000", "Auvergne-Rhône-Alpes", 3),
    ("Dijon", "21000", "Bourgogne-Franche-Comté", 2), ("Rouen", "76000", "Normandie", 3),
    ("Tours", "37000", "Centre-Val de Loire", 2), ("Metz", "57000", "Grand Est", 2),
    ("Brest", "29200", "Bretagne", 2), ("Caen", "14000", "Normandie", 2), ("Clermont-Ferrand", "63000",
                                                                          "Auvergne-Rhône-Alpes", 2),
    ("Ajaccio", "20000", "Corse", 1),
]
FOREIGN = [("Barcelona", "Spain", "ES"), ("Milano", "Italy", "IT"), ("Berlin", "Germany", "DE"),
           ("London", "United Kingdom", "GB"), ("Bruxelles", "Belgium", "BE"), ("Lisboa", "Portugal", "PT"),
           ("New York", "United States", "US"), ("Marrakech", "Morocco", "MA"), ("Genève", "Switzerland", "CH")]
RISKY_FOREIGN = [("Lagos", "Nigeria", "NG"), ("Bangkok", "Thailand", "TH"), ("Ciudad de Panamá", "Panama", "PA"),
                 ("Kyiv", "Ukraine", "UA"), ("Manila", "Philippines", "PH")]
# MCC, label, share of spend events, lognormal (mu, sigma) of amount in EUR, online probability
MCC_PROFILE = [
    (5411, "Supermarché", 0.24, 3.4, 0.6, 0.05), (5812, "Restaurant", 0.11, 3.2, 0.5, 0.02),
    (5814, "Restauration rapide", 0.08, 2.4, 0.4, 0.05), (5541, "Station-service", 0.07, 3.8, 0.35, 0.0),
    (4111, "Transport", 0.06, 2.3, 0.6, 0.45), (5912, "Pharmacie", 0.05, 3.0, 0.6, 0.05),
    (5311, "Grand magasin", 0.04, 3.9, 0.7, 0.2), (5651, "Habillement", 0.05, 4.0, 0.6, 0.45),
    (5732, "Électronique", 0.02, 5.0, 0.9, 0.55), (4900, "Énergie & télécom", 0.04, 4.0, 0.5, 0.9),
    (5999, "Commerce divers", 0.06, 3.3, 0.8, 0.4), (7011, "Hôtel", 0.015, 4.8, 0.6, 0.6),
    (4511, "Compagnie aérienne", 0.01, 5.3, 0.6, 0.95), (5942, "Librairie", 0.02, 3.0, 0.5, 0.4),
    (5815, "Services numériques", 0.05, 2.5, 0.6, 1.0), (5300, "Grossiste", 0.02, 4.2, 0.7, 0.1),
    (6011, "Retrait DAB", 0.035, 4.0, 0.5, 0.0), (5045, "Informatique", 0.01, 5.2, 0.8, 0.7),
    (7832, "Cinéma", 0.015, 2.6, 0.3, 0.3),
]
MERCHANT_WORDS = {
    5411: ["Marché", "Épicerie", "Primeur", "Supérette"], 5812: ["Brasserie", "Bistrot", "Table", "Comptoir"],
    5814: ["Snack", "Burger", "Pizzeria", "Kebab"], 5541: ["Station", "Relais"], 4111: ["Mobilité", "Navette"],
    5912: ["Pharmacie"], 5311: ["Galeries", "Magasins"], 5651: ["Boutique", "Mode", "Atelier"],
    5732: ["High-Tech", "Électro"], 4900: ["Énergie", "Télécom"], 5999: ["Bazar", "Comptoir", "Maison"],
    7011: ["Hôtel", "Résidence"], 4511: ["Air", "Aéro"], 5942: ["Librairie"], 5815: ["Stream", "Cloud", "Appli"],
    5300: ["Dépôt", "Entrepôt"], 6011: ["DAB"], 5045: ["Informatique", "Systèmes"], 7832: ["Cinéma"],
    7995: ["Jeux", "Paris en ligne"], 4829: ["Transfert", "Change"], 5944: ["Bijouterie"],
}
TOPICS = {  # DEMO customer message templates (English; topic classifier trained on BANKING77)
    "card_lost": ["I think I lost my card somewhere in {city}, can you block it?", "My card was stolen yesterday, what should I do?"],
    "transfer_pending": ["My transfer to my landlord has been pending for three days", "Why is the transfer I made on Monday still not received?"],
    "card_declined": ["My card was declined at the supermarket even though I have money", "Payment refused online, why?"],
    "unknown_payment": ["There is a payment I don't recognise on my statement from {merchant}", "I see a charge I did not make, please help"],
    "fees": ["Why was I charged a fee for withdrawing cash?", "I was charged an extra fee on my last card payment abroad"],
    "refund": ["I returned an item but the refund has not arrived yet", "How long does a refund take to show on my account?"],
    "pin": ["I forgot my PIN, how can I see it?", "My card is blocked after entering the wrong PIN"],
    "top_up": ["My top up did not work", "I tried to add money with my card but it failed"],
    "exchange_rate": ["Which exchange rate do you use for payments in dollars?", "The exchange rate applied was wrong"],
    "account_info": ["How do I change my address in the app?", "Where can I find my account details for a salary payment?"],
}


@dataclass
class _Card:
    id: uuid.UUID
    customer_id: uuid.UUID
    city: str
    region: str
    rate: float
    online_bias: float


def _merchant_catalog(rng: random.Random) -> list[dict[str, Any]]:
    merchants = []
    mid = 100000
    for mcc, label, *_ in [*MCC_PROFILE, (7995, "Jeux", 0, 0, 0, 1), (4829, "Transfert", 0, 0, 0, 1),
                           (5944, "Bijouterie", 0, 0, 0, 0)]:
        for city, _pc, region, _w in CITIES:
            for _ in range(2 if mcc in (5411, 5812, 5814) else 1):
                word = rng.choice(MERCHANT_WORDS.get(mcc, [label]))
                mid += 1
                merchants.append({"id": f"DM{mid}", "name": f"{word} {rng.choice(LAST)} (DEMO)", "mcc": mcc,
                                  "city": city, "region": region, "country": "FR"})
        for city, country_name, code in FOREIGN + RISKY_FOREIGN:
            mid += 1
            merchants.append({"id": f"DM{mid}", "name": f"{label} {city} (DEMO)", "mcc": mcc, "city": city,
                              "region": country_name, "country": code})
    return merchants


def generate_demo_bank(org_slug: str = "demo-bank", seed: int = 2026, n_customers: int = 1500, days: int = 120,
                       end: datetime | None = None) -> dict[str, Any]:
    rng = random.Random(seed)
    nrng = np.random.default_rng(seed)
    end = (end or datetime.now(UTC)).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    with control_session() as db:
        org = db.execute(select(Organization).where(Organization.slug == org_slug)).scalar_one()
        if org.kind not in ("demo", "bank"):
            raise ValueError("DEMO data can only be generated for demo/test organisations")
        target = TenantTarget(org.id, org.isolation_mode, org.dedicated_db_secret)
        org_id = org.id

    merchants = _merchant_catalog(rng)
    by_mcc_city: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for m in merchants:
        by_mcc_city.setdefault((m["mcc"], m["city"]), []).append(m)
    city_weights = [c[3] for c in CITIES]

    customers, accounts, cards, card_objs = [], [], [], []
    for i in range(n_customers):
        is_business = rng.random() < 0.08
        city, pc, region, _ = rng.choices(CITIES, weights=city_weights)[0]
        cid = uuid.uuid4()
        segment = "sme" if is_business else rng.choices(["mass_retail", "young", "affluent", "senior"],
                                                          weights=[55, 20, 12, 13])[0]
        birth = {"young": rng.randint(1997, 2006), "senior": rng.randint(1940, 1960), "affluent": rng.randint(1960, 1990),
                 "mass_retail": rng.randint(1960, 1998), "sme": rng.randint(1955, 1990)}[segment]
        income = float(round(nrng.lognormal({"young": 9.9, "senior": 10.1, "affluent": 11.3, "mass_retail": 10.3,
                                             "sme": 11.8}[segment], 0.35), -2))
        name = (f"{rng.choice(LAST)} {rng.choice(['Conseil', 'Bâtiment', 'Logistique', 'Commerce', 'Studio'])} SAS"
                if is_business else f"{rng.choice(FIRST)} {rng.choice(LAST)}")
        customers.append({
            "id": cid, "org_id": org_id, "external_ref": f"DEMO-C-{i + 1:06d}", "full_name": name,
            "customer_type": "business" if is_business else "individual", "segment": segment,
            "birth_year": None if is_business else birth, "country": "FR", "city": city, "postal_code": pc,
            "occupation": None if is_business else rng.choice(["Employé", "Cadre", "Ouvrier", "Indépendant",
                                                                "Étudiant", "Retraité", "Fonctionnaire"]),
            "annual_income": income, "risk_rating": rng.choices(["low", "standard", "high"], weights=[30, 65, 5])[0],
            "kyc_status": rng.choices(["verified", "to_review"], weights=[96, 4])[0], "pep": rng.random() < 0.005,
            "onboarded_at": (start - timedelta(days=rng.randint(30, 4000))).date(), "is_demo": True,
        })
        n_acc = 2 if rng.random() < 0.45 else 1
        for k in range(n_acc):
            aid = uuid.uuid4()
            accounts.append({"id": aid, "org_id": org_id, "customer_id": cid, "iban": make_fr_iban(rng),
                             "account_type": "current" if k == 0 else rng.choice(["savings", "livret"]),
                             "currency": "EUR", "opened_at": customers[-1]["onboarded_at"],
                             "balance": round(float(nrng.lognormal(7.5 if k == 0 else 8.5, 1.0)), 2),
                             "status": "active", "is_demo": True})
            if k == 0 and (rng.random() < 0.93 or is_business):
                card_id = uuid.uuid4()
                cards.append({"id": card_id, "org_id": org_id, "account_id": aid, "customer_id": cid,
                              "card_ref": f"DEMO-****-{rng.randint(1000, 9999)}", "card_index": 0,
                              "brand": rng.choice(["visa", "mastercard"]),
                              "card_type": "credit" if segment == "affluent" and rng.random() < 0.5 else "debit",
                              "credit_limit": None, "has_chip": True, "status": "active", "home_zip": pc,
                              "home_state": region, "is_demo": True})
                rate = {"young": 1.6, "affluent": 1.5, "senior": 0.8, "mass_retail": 1.2, "sme": 2.2}[segment]
                card_objs.append(_Card(card_id, cid, city, region, rate * float(nrng.uniform(0.6, 1.4)),
                                       {"young": 0.35, "affluent": 0.25, "senior": 0.08, "mass_retail": 0.18,
                                        "sme": 0.3}[segment]))

    # ------------------------------------------------------------------ card transactions
    txs: list[dict[str, Any]] = []
    mcc_codes = [m[0] for m in MCC_PROFILE]
    mcc_w = [m[2] for m in MCC_PROFILE]
    prof = {m[0]: m for m in MCC_PROFILE}
    total_minutes = days * 24 * 60

    def pick_merchant(mcc: int, city: str) -> dict[str, Any]:
        pool = by_mcc_city.get((mcc, city)) or by_mcc_city.get((mcc, "Paris"))
        return rng.choice(pool)  # type: ignore[arg-type]

    def add_tx(card: _Card, ts: datetime, amount: float, mcc: int, merchant: dict[str, Any], online: bool,
               errors: str | None = None, scenario: str | None = None, fraud: bool | None = None,
               swipe: bool = False) -> None:
        txs.append({
            "id": uuid.uuid4(), "org_id": org_id, "card_id": card.id, "customer_id": card.customer_id, "ts": ts,
            "amount": round(amount, 2), "currency": "EUR",
            "use_chip": "Online Transaction" if online else ("Swipe Transaction" if swipe else "Chip Transaction"),
            "merchant_id": merchant["id"], "merchant_name": merchant["name"],
            "merchant_city": "ONLINE" if online else merchant["city"],
            "merchant_state": None if online else merchant["region"],
            "merchant_zip": None, "mcc": mcc, "errors": errors, "sim_scenario": scenario,
            "sim_is_fraud": bool(fraud) if scenario else False, "is_demo": True, "source": "stream",
        })

    for card in card_objs:
        n = int(nrng.poisson(card.rate * days))
        minutes = np.sort(nrng.integers(0, total_minutes, size=n))
        travel_until: datetime | None = None
        travel_city: tuple[str, str, str] | None = None
        for mnt in minutes:
            ts = start + timedelta(minutes=int(mnt))
            hour = ts.hour
            if hour < 6 and rng.random() < 0.85:  # most legitimate activity happens during the day
                ts = ts.replace(hour=rng.randint(8, 21))
            mcc = rng.choices(mcc_codes, weights=mcc_w)[0]
            _, _, _, mu, sigma, p_online = prof[mcc]
            online = rng.random() < max(p_online, card.online_bias if p_online > 0 else 0)
            if travel_until is None and rng.random() < 0.002:
                travel_until = ts + timedelta(days=rng.randint(3, 9))
                f = rng.choice(FOREIGN) if rng.random() < 0.4 else None
                c = rng.choice(CITIES)
                travel_city = (f[0], f[1], f[2]) if f else (c[0], c[2], "FR")
            city = card.city
            if travel_until and ts <= travel_until and travel_city and not online:
                city = travel_city[0]
            elif travel_until and ts > travel_until:
                travel_until, travel_city = None, None
            merchant = pick_merchant(mcc, city) if city in {c[0] for c in CITIES} else rng.choice(
                [m for m in merchants if m["city"] == city and m["mcc"] == mcc] or [pick_merchant(mcc, card.city)])
            amount = float(nrng.lognormal(mu, sigma))
            errors = None
            if rng.random() < 0.012:
                errors = rng.choices(["Insufficient Balance", "Bad PIN", "Technical Glitch"], weights=[6, 2, 2])[0]
            if rng.random() < 0.004:
                amount = -round(amount * rng.uniform(0.3, 1.0), 2)  # refund
            add_tx(card, ts, amount, mcc, merchant, online, errors, swipe=rng.random() < 0.03)

    # ------------------------------------------------------------------ simulated fraud scenarios
    fraud_cards = rng.sample(card_objs, k=max(6, len(card_objs) // 120))
    scenarios_count: dict[str, int] = {}
    for idx, card in enumerate(fraud_cards):
        kind = ["card_testing", "stolen_card_abroad", "account_takeover_online", "pin_attack_cash"][idx % 4]
        when = start + timedelta(days=rng.randint(35, days - 2), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
        scenarios_count[kind] = scenarios_count.get(kind, 0) + 1
        if kind == "card_testing":
            for j in range(rng.randint(5, 9)):
                m = rng.choice([x for x in merchants if x["mcc"] in (5815, 5999)])
                add_tx(card, when + timedelta(minutes=2 * j), rng.uniform(0.5, 4.0), m["mcc"], m, True,
                       "Bad CVV" if rng.random() < 0.4 else None, kind, True)
            m = rng.choice([x for x in merchants if x["mcc"] in (5732, 5045)])
            add_tx(card, when + timedelta(minutes=25), rng.uniform(900, 2400), m["mcc"], m, True, None, kind, True)
        elif kind == "stolen_card_abroad":
            city, country_name, code = rng.choice(RISKY_FOREIGN)
            pool = [x for x in merchants if x["city"] == city and x["mcc"] in (5944, 5732, 6011, 4829)]
            for j in range(rng.randint(3, 6)):
                m = rng.choice(pool)
                add_tx(card, when.replace(hour=rng.randint(0, 4)) + timedelta(minutes=17 * j),
                       rng.uniform(300, 1800), m["mcc"], m, False, None, kind, True, swipe=True)
        elif kind == "account_takeover_online":
            pool = [x for x in merchants if x["mcc"] in (7995, 4829) and x["country"] in ("FR", "NG", "PA")]
            for j in range(rng.randint(3, 5)):
                m = rng.choice(pool)
                add_tx(card, when + timedelta(minutes=9 * j), rng.uniform(400, 2500), m["mcc"], m, True, None, kind, True)
        else:
            m = rng.choice([x for x in merchants if x["mcc"] == 6011 and x["country"] == "FR" and x["city"] != card.city])
            for j in range(3):
                add_tx(card, when + timedelta(minutes=j), 0.0, 6011, m, False, "Bad PIN", kind, True)
            add_tx(card, when + timedelta(minutes=5), rng.uniform(400, 900), 6011, m, False, None, kind, True)

    # ------------------------------------------------------------------ transfers (+ AML typologies)
    transfers: list[dict[str, Any]] = []
    cust_by_id = {c["id"]: c for c in customers}
    current_accounts = [a for a in accounts if a["account_type"] == "current"]

    def add_transfer(src: str | None, dst: str | None, ts: datetime, amount: float, channel: str = "sepa",
                     direction: str = "internal", cp_iban: str | None = None, cp_name: str | None = None,
                     cp_country: str | None = None, ref: str | None = None, typology: str | None = None) -> uuid.UUID:
        tid = uuid.uuid4()
        transfers.append({"id": tid, "org_id": org_id, "src_account_id": src, "dst_account_id": dst,
                          "counterparty_iban": cp_iban, "counterparty_name": cp_name, "counterparty_country": cp_country,
                          "ts": ts, "amount": round(amount, 2), "currency": "EUR", "channel": channel,
                          "direction": direction, "reference": ref, "sim_typology": typology, "is_demo": True})
        return tid

    employers = [(f"{rng.choice(LAST)} {rng.choice(['Industries', 'Services', 'Groupe', 'Santé'])} (DEMO)",
                  make_fr_iban(rng, bank="99998")) for _ in range(40)]
    for acc in current_accounts:
        cust = cust_by_id[acc["customer_id"]]
        monthly = (cust["annual_income"] or 24000) / 12
        emp_name, emp_iban = rng.choice(employers)
        landlord_iban = make_fr_iban(rng, bank="99997")
        for month in range(math.ceil(days / 30) + 1):
            pay_day = start + timedelta(days=30 * month + rng.randint(0, 2), hours=rng.randint(6, 10))
            if pay_day > end:
                break
            if (cust["customer_type"] == "individual" and cust["segment"] != "young") or rng.random() < 0.5:
                add_transfer(None, acc["id"], pay_day, monthly * rng.uniform(0.95, 1.05), direction="in",
                             cp_iban=emp_iban, cp_name=emp_name, cp_country="FR",
                             ref="SALAIRE" if cust["customer_type"] == "individual" else "FACTURE CLIENT")
            if rng.random() < 0.7:
                add_transfer(acc["id"], None, pay_day + timedelta(days=rng.randint(2, 6)), monthly * rng.uniform(0.25, 0.4),
                             direction="out", cp_iban=landlord_iban, cp_name="Bailleur (DEMO)", cp_country="FR",
                             ref="LOYER")
        # occasional P2P transfers between DEMO customers
        for _ in range(int(nrng.poisson(2))):
            other = rng.choice(current_accounts)
            if other["id"] != acc["id"]:
                add_transfer(acc["id"], other["id"], start + timedelta(minutes=int(nrng.integers(0, total_minutes))),
                             float(nrng.lognormal(4.0, 0.8)), channel=rng.choice(["sepa", "instant"]),
                             ref=rng.choice(["Remboursement", "Cadeau", "Participation", "Vacances"]))

    aml_pool = rng.sample(current_accounts, k=min(60, len(current_accounts)))
    typology_count: dict[str, int] = {}
    cursor = 0

    def take(k: int) -> list[dict[str, Any]]:
        nonlocal cursor
        chunk = aml_pool[cursor:cursor + k]
        cursor += k
        return chunk

    offshore = [("AE", "Trading FZE (DEMO)"), ("MT", "Consulting Ltd (DEMO)"), ("CY", "Holdings Ltd (DEMO)")]
    for rep in range(2):  # fan-in with structuring below 10k€ then wire abroad
        hub, *senders = take(9)
        t0 = start + timedelta(days=rng.randint(40, days - 15))
        for s in senders:
            add_transfer(s["id"], hub["id"], t0 + timedelta(hours=rng.randint(0, 96)), rng.uniform(8800, 9950),
                         channel="instant", ref=rng.choice(["Prêt", "Avance", "Remboursement"]), typology="fan_in")
        country, name = rng.choice(offshore)
        add_transfer(hub["id"], None, t0 + timedelta(days=5), 8 * 9300, channel="swift", direction="out",
                     cp_iban=make_foreign_iban(rng, country), cp_name=name, cp_country=country, ref="INVOICE 2024-" + str(rep),
                     typology="fan_in")
        typology_count["fan_in"] = typology_count.get("fan_in", 0) + 1
    for _ in range(2):  # cycles A→B→C→D→A
        ring = take(4)
        t0 = start + timedelta(days=rng.randint(40, days - 10))
        amount = rng.uniform(15000, 40000)
        for j, acc in enumerate(ring):
            amount *= rng.uniform(0.97, 0.995)
            add_transfer(acc["id"], ring[(j + 1) % len(ring)]["id"], t0 + timedelta(hours=8 * j), amount,
                         ref="Prestation", typology="cycle")
        typology_count["cycle"] = typology_count.get("cycle", 0) + 1
    for _ in range(2):  # fan-out (distribution to mules)
        src, *mules = take(7)
        t0 = start + timedelta(days=rng.randint(40, days - 10))
        add_transfer(None, src["id"], t0, 60000, channel="swift", direction="in",
                     cp_iban=make_foreign_iban(rng, "AE"), cp_name="Import Export LLC (DEMO)", cp_country="AE",
                     ref="CONTRACT", typology="fan_out")
        for m in mules:
            add_transfer(src["id"], m["id"], t0 + timedelta(hours=rng.randint(2, 30)), rng.uniform(7000, 9500),
                         channel="instant", typology="fan_out")
        typology_count["fan_out"] = typology_count.get("fan_out", 0) + 1
    for _ in range(3):  # cash structuring
        acc = take(1)[0]
        t0 = start + timedelta(days=rng.randint(40, days - 12))
        for d in range(rng.randint(5, 8)):
            add_transfer(None, acc["id"], t0 + timedelta(days=d, hours=rng.randint(9, 17)), rng.uniform(9000, 9990),
                         channel="cash", direction="in", ref="DEPOT ESPECES", typology="structuring")
        typology_count["structuring"] = typology_count.get("structuring", 0) + 1
    for _ in range(3):  # rapid pass-through
        acc = take(1)[0]
        t0 = start + timedelta(days=rng.randint(40, days - 5))
        amt = rng.uniform(25000, 80000)
        add_transfer(None, acc["id"], t0, amt, channel="sepa", direction="in", cp_iban=make_foreign_iban(rng, "DE"),
                     cp_name="Unbekannt GmbH (DEMO)", cp_country="DE", ref="VIREMENT", typology="pass_through")
        country, name = rng.choice(offshore)
        add_transfer(acc["id"], None, t0 + timedelta(hours=rng.randint(4, 30)), amt * rng.uniform(0.92, 0.99),
                     channel="swift", direction="out", cp_iban=make_foreign_iban(rng, country), cp_name=name,
                     cp_country=country, ref="PAYMENT", typology="pass_through")
        typology_count["pass_through"] = typology_count.get("pass_through", 0) + 1

    # ------------------------------------------------------------------ credit applications (UCI-compatible schema)
    applications = []
    applicants = rng.sample([c for c in customers if c["customer_type"] == "individual"], k=min(220, n_customers // 6))
    for i, cust in enumerate(applicants):
        risk = rng.random()
        late = 0 if risk < 0.6 else (1 if risk < 0.8 else rng.randint(2, 4))
        limit = float(round(rng.choice([1000, 1500, 2000, 3000, 5000, 8000, 10000]) * (1.6 if cust["segment"] == "affluent" else 1)))
        bills = [max(0.0, limit * rng.uniform(0.05, 0.95)) for _ in range(6)]
        pays = [b * (rng.uniform(0.02, 0.15) if late >= 2 else rng.uniform(0.1, 1.0)) for b in bills]
        statuses = [min(8, max(-2, late + rng.choice([-1, 0, 0, 1]) if late else rng.choice([-1, -1, 0, 0, -2]))) for _ in range(6)]
        applications.append({
            "id": uuid.uuid4(), "org_id": org_id, "reference": f"DEMO-CR-{i + 1:05d}", "customer_id": cust["id"],
            "product": "credit_card", "requested_limit": limit, "status": "submitted", "is_demo": True,
            "features": {"currency": "EUR", "limit_bal": limit, "education": rng.choice([1, 2, 2, 3]),
                         "pay_status": statuses, "bill_amt": [round(b, 2) for b in bills],
                         "pay_amt": [round(p, 2) for p in pays], "age": 2026 - (cust["birth_year"] or 1980),
                         "annual_income": cust["annual_income"]},
        })

    # ------------------------------------------------------------------ customer messages (DEMO templates)
    messages = []
    topic_keys = list(TOPICS)
    for _ in range(min(700, n_customers // 2)):
        cust = rng.choice(customers)
        topic = rng.choice(topic_keys)
        text = rng.choice(TOPICS[topic]).format(city=cust["city"], merchant=rng.choice(merchants)["name"])
        messages.append({"id": uuid.uuid4(), "org_id": org_id, "customer_id": cust["id"],
                         "received_at": start + timedelta(minutes=int(nrng.integers(0, total_minutes))),
                         "channel": rng.choice(["app", "email", "chat"]), "text": text, "is_demo": True})

    # ------------------------------------------------------------------ write (replace previous DEMO data)
    with tenant_session(target, owner=True) as s:
        for table in reversed(TenantBase.metadata.sorted_tables):
            if "is_demo" in table.c:
                s.execute(delete(table).where(table.c.org_id == org_id, table.c.is_demo.is_(True)))
            elif table.name in ("fraud_alerts", "aml_alerts", "aml_account_scores", "case_events", "cases",
                                "customer_segments", "inference_log"):
                s.execute(delete(table).where(table.c.org_id == org_id))
        for model, rows in ((Customer, customers), (Account, accounts), (Card, cards), (CardTransaction, txs),
                            (Transfer, transfers), (CreditApplication, applications), (CustomerMessage, messages)):
            for k in range(0, len(rows), 5000):
                s.execute(insert(model), rows[k:k + 5000])
    summary = {
        "org": org_slug, "seed": seed, "period": {"start": start.isoformat(), "end": end.isoformat()},
        "customers": len(customers), "accounts": len(accounts), "cards": len(cards), "card_transactions": len(txs),
        "transfers": len(transfers), "credit_applications": len(applications), "customer_messages": len(messages),
        "simulated_fraud_scenarios": scenarios_count,
        "simulated_fraud_transactions": sum(1 for t in txs if t["sim_is_fraud"]),
        "simulated_aml_typologies": typology_count,
    }
    audit.record("demo.generated", org_id=org_id, actor_type="system", actor_label="demo-generator", details=summary)
    return summary


_ = (date,)
