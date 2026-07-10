---
title: "Vessel Flags and AIS Signals: A Field Guide to Reading Shadow-Fleet Behaviour"
slug: "vessel-flags-and-ais-signals"
description: "How flag states, AIS identifiers and behavioural red flags reveal shadow-fleet, narco, and IUU-fishing vessels - a plain-language OSINT reference."
date: "2026-07-10"
tags: ["AIS", "shadow fleet", "maritime OSINT", "sanctions", "IUU fishing", "vessel tracking"]
author: "Dark Ships"
---

Every ship broadcasts a story about itself. Its flag, its identity numbers, its position and its behaviour are all public data, freely transmitted over the airwaves and recorded by shore stations and satellites. Most of that story is honest. But a small, deliberate fraction of the world fleet uses those same signals to lie - to hide who owns a vessel, where it has been, and what it is carrying.

This is a working reference to the terms and signals Dark Ships uses to separate the two. It is written for journalists, researchers, and analysts who want to understand what a "flag of convenience" actually buys, how the AIS system works, and what each behavioural red flag on the map really means. None of these signals is a verdict - they are leads. But stacked together, they are how a hull that is trying to disappear gives itself away.

## Flag states and flags of convenience

Every merchant vessel is registered to a country: its **flag state**. In principle the flag state is responsible for the ship - for inspecting it, enforcing safety and labour standards, and holding its owners accountable. In practice, a large share of the world fleet flies a flag that has nothing to do with where the ship operates or who owns it. This is a **flag of convenience**: a registry that a shipowner chooses because it is cheap, fast, anonymous, and undemanding.

Open registries like Panama, Liberia and the Marshall Islands are the best-known examples, and most of the ships flying them are entirely legitimate. But the same features that attract ordinary commercial operators - minimal disclosure of beneficial ownership, light-touch inspection, easy registration - are exactly what a sanctions-evading operator needs. When one registry tightens its rules or comes under diplomatic pressure, a **shadow-fleet** vessel simply re-registers somewhere more permissive. Watching a hull move through a string of small, obscure flags over a short period is itself a warning sign.

Why the flag matters for detection:

- **Accountability follows the flag.** A vessel's real-world consequences - inspections, detentions, insurance, port access - are tied to the state that flags it.
- **Flag-hopping is a laundering tactic.** Rapid reflagging lets a ship shed a sanctioned or detained history and present itself as clean.
- **Some flags cluster with risk.** When a registry is dominated by ageing tankers with opaque ownership, that pattern shows up in the data long before any single ship is named.

## AIS basics: MMSI, IMO, MID and ship type

The **Automatic Identification System (AIS)** is a short-range radio system that ships use to broadcast their identity, position, course and speed so that others can avoid collisions. It was designed for safety, not surveillance - which is precisely why it is such a rich intelligence source. Every message a vessel transmits is public, and any receiver in range, on shore or in orbit, can log it.

A few identifiers do most of the work:

- **MMSI (Maritime Mobile Service Identity)** - a nine-digit number tied to the ship's radio equipment. It is the primary key for most tracking, but it can be changed, cloned, or faked, so it is not a reliable long-term identity on its own.
- **IMO number** - a seven-digit number assigned to the *hull* for its entire life. Unlike the MMSI, it is not supposed to change when a ship is sold or reflagged, which makes it the anchor for spotting the same physical vessel behind many identities.
- **MID (Maritime Identification Digits)** - the first three digits of the MMSI encode the country of the vessel's flag. A ship claiming a MID that does not exist, or that contradicts its known flag, is broadcasting a fabricated identity.
- **Ship type** - AIS carries a coarse vessel-type code (tanker, cargo, fishing, passenger, and so on). It is self-reported, so a tanker declaring itself as something innocuous is a small red flag in its own right.

Because every one of these fields is self-declared, AIS is as useful for catching lies as it is for tracking honest ships. A hull's true identity is the IMO; everything a spoofer changes around it - MMSI, name, callsign, flag - leaves a trail.

## The risk categories Dark Ships tracks

Before drilling into individual behaviours, it helps to know the broad missions these vessels serve. Dark Ships sorts flagged vessels into a handful of categories:

- **Shadow fleet** - sanctions-evading oil transport, chiefly moving Russian, Iranian, Venezuelan and North Korean crude outside the reach of Western sanctions and insurance.
- **Narco** - drug trafficking by sea, from purpose-built low-profile vessels to legitimate cargo ships used as cover.
- **IUU fishing** - illegal, unreported and unregulated fishing, including boats operating in closed areas, under false identities, or on official blacklists.
- **Sabotage** - vessels linked to damage to undersea cables and pipelines, or acting as intelligence-gathering "spy ships" near sensitive infrastructure.
- **Smuggling** - moving arms, contraband or waste, including illegal dumping at sea.

A vessel's category is context; the signals below are the evidence that puts it there.

## Sanctions and official lists: the hardest signals

The strongest thing that can be said about a ship is that a government or international body has named that exact hull. These are designations, not guesses, and on their own they are enough to put a vessel on the watchlist:

- **OFAC sanctions list** - on the US Treasury's Specially Designated Nationals list. The strongest single signal.
- **UK / EU sanctions lists** - a formal UK FCDO designation, or naming in the EU's port-ban / sanctions package. Banned or restricted across those jurisdictions.
- **GUR shadow-fleet database** - Ukraine's military-intelligence shadow-fleet data, the largest structured list of sanctions-evading tankers.
- **UN (DPRK) 1718 list** - a UN Security Council designation tied to North Korean sanctions evasion.
- **UANI Iran-tanker list** - United Against Nuclear Iran's tracker of tankers moving sanctioned Iranian oil.
- **Allied lists** - Canada (SEMA), Australia (DFAT) and Switzerland (SECO) designations, which add net-new hulls beyond the US/EU/UK lists.
- **Paris MoU port ban** - refused access to European ports. Strong, though not a full sanction.

### Detentions: corroboration only

A separate class of official record is the **port-state-control detention** - a Tokyo MoU, Black Sea MoU or Abuja MoU inspection that held the ship for being substandard. There are thousands of these, and a detention is not proof of a crime, so it never puts a vessel on the watchlist by itself. It only strengthens a case when the ship is already flagged for something else - and the region matters: the Black Sea points at sanctions evasion, West Africa at narco, oil theft and illegal fishing.

### Illegal-fishing blacklists

For IUU fishing, the equivalent hard signals are the **IUU illegal-fishing blacklist** (a combined RFMO list from bodies like CCAMLR and ICCAT) and the **EU IUU fishing list**, the EU-legal version. Both name vessels documented fishing illegally.

## Behavioural red flags: how ships betray themselves

Sanctions lists tell you what is already known. The behavioural signals below are how Dark Ships surfaces vessels *before* anyone has named them - by watching what they do.

### Going dark (AIS gaps)

**Going dark** means a ship stops transmitting AIS when we would expect to hear it. It is the classic move before a covert transfer or a sanctioned port call.

- **Went dark in coverage** - the vessel fell silent while underway inside solid receiver coverage, not at the edge of range. A ship that switches off its transponder mid-voyage is usually hiding a movement.
- **Disabled AIS (GFW)** - Global Fishing Watch independently recorded an intentional AIS gap.

### Ship-to-ship (STS) transfers

An **STS transfer** is two vessels meeting at sea to move cargo - the core method for laundering sanctioned oil and moving contraband away from port oversight.

- **Possible ship-to-ship** - two tankers sat within a few hundred metres of each other, near-stationary and offshore, both recently underway: the geometry of an at-sea transfer.
- **Met another vessel at sea** - a GFW-logged encounter (transshipment) event.
- **Changed draught at sea** - the reported draught (how deep the hull sits) changed near a known transfer zone, meaning it loaded or unloaded offshore rather than at a port.
- **Oil slick at transfer point** - satellite radar saw a slick at the meeting location: near-conclusive physical evidence of an oil transfer.
- **Anchored among sanctioned ships** - sat at anchor in a cluster of sanctioned or shadow-fleet vessels. Company kept: these fleets gather in the same transfer anchorages.

### Spoofing and identity manipulation

These are deliberate deceptions - a strong signal that something is being hidden - and several are enough to flag a vessel on their own.

- **Identity change** - broadcast a different name, IMO or callsign than before. The classic way to shed a sanctioned history.
- **One identity in two places (MMSI collision)** - the same MMSI transmitted two separate tracks at once. A cloned identity, two hulls hiding behind one legitimate ID.
- **GPS circle-spoofing** - a fake, near-perfect circular track while claiming to move: a hallmark of GNSS manipulation.
- **Impossible jump** - consecutive positions implying an impossible speed (over 40 knots), meaning the track was tampered with or a position was injected.
- **Fabricated identity** - an invalid or factory-default MMSI, or a bad country (MID) prefix: a core identity-laundering signature.
- **Reflagged repeatedly (flag-hopping)** - one hull (IMO) seen under many different MMSIs and flags, used to stay ahead of sanctions and inspections.
- **Claims anchored while moving** - declared "at anchor" or "moored" while clearly under way. Sometimes a stale crew setting, but also a way to look innocent to automated filters.

### Softer patterns: loitering and dark rendezvous

On their own these are weak - they corroborate rather than accuse - but stacked with a hard signal they raise the risk score:

- **Loitering offshore** - held position, near-stationary, well offshore in a known trafficking corridor, consistent with a hand-off or covert meeting (also confirmed independently via GFW loitering events).
- **Appeared mid-sea** - a brand-new identity first seen in open water rather than leaving a port. Often just where the ship was first received, so used only to corroborate.
- **Reappeared off predicted drift** - after going dark, a vessel reappeared far from where currents alone would have carried it, suggesting it was under power during the gap rather than merely out of range. When a going-dark event is paired with a reappearance next to another ship, that is a **dark rendezvous** - the strongest circumstantial case for a covert transfer.

## Reading the signals: leads, not verdicts

None of these flags is a conviction. A sanctions match is a hard fact about a hull; loitering or a single AIS gap has innocent explanations too. That is why Dark Ships assigns each flag a weight and sums a vessel's flags over a rolling window into a **risk score** - so a ship with several corroborating signals rises above one with a single weak one. A vessel only lands on the watchlist automatically when it has a *hard anchor*: an official designation, or a deliberate deception like an identity change, a cloned MMSI, GPS spoofing, an impossible jump, a fabricated identity, or a draught change at sea. Soft patterns and detentions only add to the score.

All of it is built from free, public data: worldwide AIS, government and NGO sanctions and fishing lists, Global Fishing Watch behavioural events, and Sentinel-1/2 satellite imagery for cross-checks.

## See it live

The vocabulary only comes alive against real tracks. Open the [live map](https://darkships.org) to watch these signals as they are raised - flagged tankers going dark in the Black Sea, rendezvous clusters off West Africa, and reflagged hulls trying to outrun their own history. Every flag on a vessel links back to the plain-language definition of what it means and why it matters.
