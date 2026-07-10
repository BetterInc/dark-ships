---
title: "Who Watches the Shadow Fleet: A Field Guide to the Open-Source Maritime Intelligence Community"
slug: "who-tracks-shadow-fleets"
description: "A field guide to the people who track shadow-fleet, sanctions-evading and IUU-fishing vessels from open data, and how Dark Ships serves each of them."
date: "2026-07-10"
tags: ["OSINT", "maritime security", "shadow fleet", "sanctions", "IUU fishing", "open source intelligence"]
author: "Dark Ships"
---

The "shadow fleet" - the roughly 1,400-plus ageing tankers moving sanctioned
Russian, Iranian, Venezuelan and North Korean oil under rotating flags and
shell owners - is not a secret. What makes it hard is not finding that these
ships exist, but watching them in motion: catching the moment a tanker switches
off its transponder near a pipeline, spots a ship-to-ship transfer offshore, or
reappears under a new name a thousand miles from where it went dark.

For most of maritime history that kind of watching required a navy or a
commercial data subscription costing tens of thousands a year. It no longer
does. A live global AIS stream, free Sentinel-1 and Sentinel-2 satellite
imagery, Global Fishing Watch behavioural events, and a stack of public
sanctions lists are enough to build real behavioural detection. Dark Ships is
built entirely on those free sources.

This post is a field guide to the people who care about that work - who they
are, what they actually need, and where an open tool fits into their workflow.

## OSINT researchers

The open-source intelligence community is the natural home of this work. These
are analysts and hobbyists who geolocate, cross-reference and verify claims from
public data, often publishing threads and reports that news organisations later
pick up. Maritime OSINT is a fast-growing corner of it, driven by the war in
Ukraine, undersea-cable incidents and the sanctions story.

**What they need:** a live map they can point at, replayable vessel tracks,
and signals they can independently verify. OSINT credibility depends on
reproducibility - a claim is only as good as the evidence anyone else can
re-check.

**How Dark Ships helps:** every position shown is a real received AIS report,
never a dead-reckoned guess, and any vessel the terrestrial feed has seen can
have its track replayed after the fact. Each detected AIS gap is paired with the
Sentinel-1 (SAR) and Sentinel-2 (optical) acquisitions that crossed the drift
area during the gap, with direct Copernicus Browser links so a researcher can
open the raw scene and judge it themselves.

## Maritime-security analysts

Working analysts - in navies, coast guards, port authorities, and private
maritime-security firms - track patterns of life: which vessels loiter where,
which corridors see ship-to-ship activity, which flags are being abused this
month. They think in behaviour, not single incidents.

**What they need:** behavioural signals separated from noise, and a way to tell
a genuinely suspicious pattern from an ordinary one.

**How Dark Ships helps:** the behaviour engine scores every vessel against
roughly 18 rules every 10 minutes - going dark inside covered water, identity
and flag changes, GNSS circle-spoofing, impossible speed jumps, draught changes
near transfer zones, loitering, and dark rendezvous. Crucially, a vessel only
joins the shared watchlist when it has a *hard anchor* - a government
designation or a hard behavioural signal - so soft signals like loitering
corroborate rather than flood the feed.

## Sanctions-compliance teams

Banks, commodity traders, insurers, and shipping companies have to prove they
are not touching a sanctioned vessel. Since the G7 oil-price-cap regime, the
burden of that due diligence has moved firmly onto the private sector.

**What they need:** to check a specific ship - by IMO or MMSI - against current
designation lists, and to see behavioural red flags (AIS gaps, spoofing,
suspicious STS) that suggest evasion even when a vessel is not yet listed.

**How Dark Ships helps:** it imports OFAC, UK, EU, Ukraine (GUR), Canada,
Australia, Switzerland, UN 1718 (DPRK), UANI and the KSE-linked lists daily,
matched against the live feed by IMO. That covers designation status; the
behaviour engine adds the pattern-of-life layer that a static list can't. (For
regulated compliance, a static list is a starting point, not a legal
determination - but it is exactly the right first filter.)

## Investigative journalists

Reporters working on sanctions evasion, arms smuggling or environmental crime
increasingly build stories around vessel movements. The best of these
investigations reconstruct a single ship's journey in forensic detail.

**What they need:** a documented, defensible evidence trail, and named case
studies they can anchor a story to.

**How Dark Ships helps:** the platform is deliberately built around documented
textbook cases - the Eagle S (IMO 9329760) and its EstLink 2 cable incident,
the Yi Peng 3 (IMO 9224984), the Andrey Dolgov toothfish operation - and stores
the underlying positions as an evidence trail for followed ships. A journalist
can trace a claimed position, find the satellite pass that covered it, and cite
the raw source.

## Think tanks and policy researchers

Institutions studying sanctions effectiveness, grey-zone conflict and maritime
governance need data at the level of trends: how large the shadow fleet is, how
flag-hopping evolves, how detentions correlate with evasion.

**What they need:** aggregate, transparent, methodologically honest data.

**How Dark Ships helps:** the Sources page shows exactly which lists feed the
engine, how many vessels each contributes, the licence, and when each last
refreshed - the kind of provenance a policy paper can footnote. The deduplicated
sanctioned-vessel count is published openly rather than hidden behind a marketing
number.

## Coast guards and NGOs on IUU fishing

Illegal, unreported and unregulated fishing is a multi-billion-dollar problem,
and the vessels involved use the same trick as the oil fleet: switching off AIS
over the fishing grounds. Enforcement agencies and conservation NGOs work this
front, often with thin budgets.

**What they need:** to spot AIS blackouts and at-sea transshipment on fishing
grounds, and to check vessels against IUU blacklists.

**How Dark Ships helps:** it folds Global Fishing Watch encounter, AIS-gap and
loitering events into the same engine, and imports the RFMO IUU blacklist and
the official EU IUU list. Because it is free, it lowers the barrier for
under-resourced enforcement and research teams specifically.

## Ship-finance and insurance risk teams

Lenders and marine insurers carry real exposure if a hull they finance or cover
turns out to be running sanctioned cargo. Their question is forward-looking: is
this vessel becoming a risk?

**What they need:** early behavioural warning, not just a designation that
arrives after the damage is done.

**How Dark Ships helps:** identity changes, flag-hopping across many MMSIs,
cloned identities and repeated dark periods are exactly the leading indicators
that precede a formal listing. Watching them gives a risk team lead time.

## The common thread

Every one of these audiences is served by the same three qualities: the data is
**free and open**, the signals are **verifiable** rather than black-box, and the
platform is honest about its **limits** - terrestrial AIS reaches about 200 km
offshore, and satellite passes are a sample, not continuous coverage. Those
constraints are stated plainly because the people who do this work seriously
would find them out anyway.

If you belong to one of these communities, the companion posts in this series
walk through [how to investigate a suspicious vessel](/blog/how-to-investigate-a-suspicious-vessel)
step by step, and [how we want to engage the OSINT maritime community](/blog/reaching-the-osint-maritime-community).
