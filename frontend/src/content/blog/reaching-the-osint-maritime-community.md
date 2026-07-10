---
title: "Reaching the Open-Source Maritime Intelligence Community: Where It Lives and How We Engage"
slug: "reaching-the-osint-maritime-community"
description: "Where the OSINT maritime-intelligence community gathers online and at events, its data-sharing norms, and how Dark Ships aims to engage responsibly."
date: "2026-07-10"
tags: ["OSINT", "maritime security", "community", "responsible disclosure", "open data", "ethics"]
author: "Dark Ships"
---

An open-source maritime-intelligence tool is only useful if the people who do
this work know it exists, trust how it was built, and can check its claims. Dark
Ships is free and built on free data on purpose - but "free" is not a
distribution strategy, and it is certainly not a substitute for behaving well in
a community that values verification over hype.

This post lays out where the open-source maritime-intelligence community
actually gathers, the norms it holds, and how we intend to engage with it. It is
as much a statement of principles as a map.

## Where the community lives

There is no single town square for maritime OSINT. It is spread across several
overlapping spaces, each with its own culture.

### Social platforms and OSINT circles

- **X (formerly Twitter)** still hosts a dense maritime-OSINT crowd - analysts
  posting vessel geolocations, cable-incident threads and sanctions
  cross-references in near-real-time. It rewards showing your work.
- **Bluesky and Mastodon** have absorbed a large, deliberate slice of the OSINT
  community that wanted a calmer, less algorithmic home. Many maritime and
  conflict-monitoring analysts now cross-post or have moved outright.
- **LinkedIn** is where the professional side lives - sanctions-compliance
  officers, marine insurers and maritime-security consultancies discuss the
  policy and enforcement angle in a more formal register.

### Forums and discussion boards

- **Reddit** communities around OSINT, geopolitics and shipping surface
  investigations and questions from a broad, non-specialist audience - useful
  for reaching people who don't yet call themselves analysts.
- **Specialist maritime forums** and long-running ship-spotting communities hold
  deep, patient expertise on hulls, flags and registries that no dataset
  captures.

### Newsletters, publications and events

- **Maritime-security and sanctions newsletters** are where serious readers go
  for synthesis. Independent analysts and think-tank programmes that study
  sanctions evasion and grey-zone maritime activity publish regularly, and a
  well-documented case study travels well in that format.
- **Conferences and workshops** on maritime security, OSINT and sanctions
  enforcement are where trust is built face-to-face. Presenting method and
  evidence - not a product pitch - is what earns credibility in those rooms.

### Code and data spaces

- **GitHub** is the connective tissue. In OSINT, an open methodology and
  reproducible tooling are a form of trust: people believe what they can inspect.
- **Open-data communities** - the users of Sentinel imagery via Copernicus,
  Global Fishing Watch, and OpenSanctions-derived lists - overlap heavily with
  maritime OSINT and share a strong culture of provenance and citation.

## The norms that matter

Reaching this community is less about which channel you post on and more about
respecting how it works. A few norms are close to non-negotiable.

**Show your sources.** Every claim should trace back to something a stranger can
re-check - a raw satellite scene, a specific AIS report, an official list entry.
This is why Dark Ships publishes its Sources page with per-list counts, licences
and refresh times, and links each detected gap to the raw Copernicus scene
rather than asking anyone to take a verdict on faith.

**Never fabricate.** Nothing corrodes trust faster than a confident claim that
falls apart on inspection. We only ever display real received positions - no
dead-reckoning, no extrapolation dressed up as observation. A gap is labelled a
gap, a corroborating signal is labelled corroboration, and a port-control
detention is not passed off as a sanction.

**State your limits plainly.** Terrestrial AIS reaches about 200 km offshore;
satellite passes are a sample, not continuous coverage; there is no automated
ship detection inside the SAR imagery yet. Saying so up front is what lets the
rest of the work be believed.

**Respect licences.** Open does not mean unconditional. Several of the sources
here are non-commercial (CC BY-NC) - the OpenSanctions-derived lists, the RFMO
IUU list, Global Fishing Watch - while the EU IUU list is official EU law and
commercial-safe. Honouring those terms, and being transparent about which is
which, is part of being a good citizen of the open-data ecosystem.

## Responsible disclosure and ethics

Vessel tracking sits close to real-world consequences, so a few ethical lines
matter more here than in most OSINT domains.

- **Vessels, not individual seafarers.** The subject of this work is ship
  behaviour and corporate ownership structures, not the private lives of crew
  members who are often themselves exploited. Keep the lens on the hull and the
  beneficial owner.
- **Signals, not verdicts.** A behavioural flag or a satellite pass is evidence
  to be weighed, not a conviction. We try to be precise about what a signal does
  and does not prove, and we invite people to check the raw source and disagree.
- **Corroborate before amplifying.** A single anomaly is a lead. Publishing it
  as fact before cross-checking - AIS against satellite, behaviour against list
  status - is how good-faith OSINT turns into misinformation. The whole design of
  the behaviour engine, requiring a hard anchor before a vessel joins the shared
  feed, encodes this restraint.
- **Take correction seriously.** In a verifiable field, being wrong in public and
  fixing it quickly builds more trust than never being challenged. We want to be
  the kind of project that welcomes a well-argued "you got this one wrong."

## How Dark Ships wants to engage

Our intended posture is simple: **be a useful, honest instrument, and let the
work speak.** That means publishing documented case studies that others can
reproduce, keeping the methodology and its limits in the open, honouring source
licences, and pointing people back to primary sources - the Copernicus scene,
the issuing authority's list - rather than asking them to trust a black box.

We would rather earn a small, sceptical, expert audience that checks our claims
than a large one that takes them on faith. If you work in any corner of this
community and you find something we got wrong, that feedback is the most valuable
thing you can send us.

If you're new to the project, start with [who watches the shadow fleet](/blog/who-tracks-shadow-fleets),
then try the practical [guide to investigating a suspicious vessel](/blog/how-to-investigate-a-suspicious-vessel).
