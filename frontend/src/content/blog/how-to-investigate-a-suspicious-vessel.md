---
title: "How to Investigate a Suspicious Vessel with Free Tools: A Step-by-Step Guide"
slug: "how-to-investigate-a-suspicious-vessel"
description: "A practical walkthrough for investigating a suspect ship using free tools: reading AIS gaps, spotting spoofing, satellite cross-checks and sanctions lookups."
date: "2026-07-10"
tags: ["OSINT", "AIS", "satellite imagery", "sanctions", "how-to", "vessel tracking", "Sentinel-1"]
author: "Dark Ships"
---

Say a vessel catches your attention - it went quiet near a subsea cable, or it
turned up in a news report, or it simply behaves oddly on the map. How do you go
from "that looks suspicious" to something defensible? This is a practical
walkthrough using Dark Ships and other free, public tools. No paid subscription
is required at any step.

Two principles run through all of it. First, **only trust real received
positions** - never a projected or dead-reckoned track. Second, **write down
what you can independently verify**, because an investigation is only as strong
as the evidence someone else can re-check.

## Step 1: Establish the vessel's identity

Every ship has several identifiers, and evasion usually starts by muddying them:

- **IMO number** - a permanent 7-digit hull identifier that should never change
  for the life of the ship. This is your anchor.
- **MMSI** - the 9-digit transponder identity. It *can* legitimately change
  (reflagging), but a hull that hops across many MMSIs is a red flag.
- **Name and callsign** - trivially changed, so treat them as labels, not proof.

Start from the IMO if you have it. In Dark Ships, the MMSI-to-IMO bridge is
built from the AIS static-data messages ships broadcast, so you can move between
the transponder identity and the permanent hull identity. Note down all of them
before you go further - mismatches between them are themselves a finding.

## Step 2: Read the AIS gaps

AIS "going dark" is the single most common evasion behaviour. But not every gap
is suspicious - a ship far offshore simply drifts out of range of coastal
receivers. The skill is telling a deliberate blackout from an innocent one.

Ask three questions of any gap:

- **Where did it go dark?** A vessel that stops transmitting inside
  well-covered coastal water, away from the edge of receiver range, is far more
  interesting than one that fades at the 200 km terrestrial limit.
- **Was it underway?** A ship that was moving and then goes silent is a
  different story from one anchored in port.
- **Where did it reappear, and does that make sense?** If a vessel resurfaces
  much farther from where it vanished than any plausible drift would allow, the
  gap wasn't passive - someone chose it.

Dark Ships opens a gap automatically when a ship that was underway falls silent
past the threshold, closes it on reappearance, and computes the displacement so
you can judge that last question quantitatively.

## Step 3: Look for spoofing, not just silence

The more sophisticated trick is not going dark but lying about position. Watch
for these patterns:

- **Impossible jumps** - consecutive positions that would require the ship to
  travel faster than it physically can (say, over 40 knots). That's a fabricated
  fix, not a fast ship.
- **Circle spoofing** - a GNSS artefact where a vessel appears to trace tight
  circles or geometric loops while it is actually working somewhere else
  entirely. Once you've seen the shape, it's unmistakable.
- **Cloned identity (MMSI collision)** - one identity broadcasting from two
  places at once. That means either a spoofer or a deliberate copy of a real
  ship's identity.
- **Nav-status lies** - broadcasting "at anchor" or "moored" while the track is
  clearly moving.

Each of these is one of the behaviour-engine rules, so on Dark Ships they surface
as scored events rather than something you have to eyeball - but knowing the
signature helps you understand *why* a vessel was flagged and explain it to
someone else.

## Step 4: Cross-check the position against free satellite imagery

This is where an AIS-only investigation becomes something stronger. AIS is
self-reported - the ship tells you where it is. Satellite imagery is
independent - it shows you where a ship *actually* was.

For any gap or suspect position, two free satellite sources matter:

- **Sentinel-1 (SAR)** sees through cloud and darkness. Ships appear as bright
  points against dark water, which makes it ideal for confirming a vessel was
  present in a drift area when it claimed to be elsewhere - or absent when it
  claimed to be there.
- **Sentinel-2 (optical)** gives you a natural-colour view when skies are clear,
  useful for confirming vessel type and, sometimes, a visible ship-to-ship
  pairing or an oil slick.

Dark Ships does the hard part - matching each gap to the Sentinel-1 GRD and
Sentinel-2 L1C acquisitions that crossed the drift area during the gap window -
and gives you a quicklook plus a direct Copernicus Browser link. From there you
open the raw scene yourself. Be honest about the limits: satellite passes are a
sample every few days, not continuous coverage, and confirming a specific bright
dot as your vessel is a human judgement, not an automated match. A pass that
covers the area and shows a vessel consistent with the drift is strong
corroboration; note it as such, not as certainty.

## Step 5: Check the sanctions and watchlists

Now check status. You want to know whether the hull is already designated, and
by whom. Query by **IMO** first (permanent), then by **MMSI**.

Dark Ships imports and matches, daily, against:

- **Sanctions / designation lists** - OFAC, UK, EU, Ukraine (GUR), Canada,
  Australia, Switzerland, UN 1718 (DPRK) and UANI.
- **IUU fishing blacklists** - the RFMO list and the official EU IUU list.
- **Port-control detentions** - Paris, Tokyo, Black Sea and Abuja MoU. Treat a
  detention as corroboration, not a designation: it means a ship was found
  substandard, which is suggestive but not the same as being sanctioned.

A hit on a sanctions list is a hard fact you can cite directly to the issuing
authority's own list. A behavioural flag with *no* list hit is also meaningful -
it may simply mean the vessel is ahead of the paperwork.

## Step 6: Assemble the evidence trail

Pull the threads together into something reproducible:

1. The identifiers (IMO, MMSI, name, callsign) and any mismatches between them.
2. The gap: where it opened, whether the ship was underway, where it closed, and
   the computed displacement.
3. Any spoofing signatures and the scored events behind them.
4. The satellite pass - which sensor, the acquisition time, and the Copernicus
   link to the raw scene - with your honest read of what it shows.
5. The sanctions / list status by IMO, citing the issuing body.

That package - self-reported movement, independent satellite corroboration, and
official-list status, each pointing back to a re-checkable source - is what turns
"looks suspicious" into an investigation someone else can stand behind.

## A note on limits

Free open data is powerful but bounded. Terrestrial AIS reaches roughly 200 km
offshore; beyond that a gap can't be distinguished from "out of range." There is
no automated ship-detection inside the SAR imagery yet, so the satellite step is
human verification. Stating those limits isn't a weakness - it's what makes the
rest of your conclusions credible.

New here? Start with [who tracks the shadow fleet](/blog/who-tracks-shadow-fleets)
for the wider picture, or read [how we engage the OSINT maritime community](/blog/reaching-the-osint-maritime-community).
