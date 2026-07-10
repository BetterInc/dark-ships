---
title: "How the Dark Ships Risk Score Works"
slug: "how-the-risk-score-works"
description: "How Dark Ships turns AIS behaviour and sanctions data into a single vessel risk score: the signal weights, the 30-day window, the per-rule cap and the watchlist threshold."
date: "2026-07-10"
tags: ["risk score", "vessel risk", "shadow fleet", "maritime OSINT", "methodology"]
author: "Dark Ships"
---

Every ship on the Dark Ships map carries a single number: its **risk score**. The bigger and brighter a dot, the higher that score. This post explains exactly how it is calculated, so the map is transparent and reproducible rather than a black box.

The short version: each thing we detect about a vessel is a *flag* with a *weight*, we add up a vessel's flags over the last 30 days (with a cap so nothing runs away), and a ship only reaches the watchlist automatically when it has a genuine hard anchor on top of a qualifying score.

## Every flag has a weight

When a detector fires on a vessel it records a risk event with a fixed weight. Weights are grouped by how hard the signal is to fake and how directly it implies wrongdoing.

**Verified facts (sanctions and blacklists) score highest:**

| Flag | Weight |
| --- | --- |
| On the OFAC sanctions list | 100 |
| On an IUU illegal-fishing blacklist (incl. the EU IUU list) | 80 |
| On another imported designation list (UK, EU, UANI, KSE, C4ADS, ...) | 80 |

**Deliberate deception is next: these are hard to fake and rarely innocent.**

| Flag | Weight |
| --- | --- |
| Cloned identity (one MMSI in two places at once) | 45 |
| Draught changed at sea near a transfer zone | 45 |
| Oil slick at a meeting point | 45 |
| Identity change (name / IMO / callsign swapped) | 40 |
| Circular GPS spoofing track | 40 |
| Impossible jump (physically impossible positions) | 35 |
| Flag-hopping (one hull, many MMSIs) | 35 |
| Fabricated identity (invalid MMSI / IMO) | 30 |

**Behavioural and corroboration signals weigh less on their own:**

| Flag | Weight |
| --- | --- |
| Went dark inside receiver coverage while underway | 30 |
| Loitering in a trafficking corridor | 30 |
| Reappeared off its predicted drift | 30 |
| Declared "anchored" while actually moving | 30 |
| Global Fishing Watch encounter or AIS gap | 30 |
| Ship-to-ship rendezvous | 25 |
| Global Fishing Watch loitering | 25 |
| Anchored among sanctioned ships | 20 |
| New identity first seen mid-ocean | 15 |

Port-state-control detentions (Paris, Tokyo, Black Sea and Abuja MoU) are treated as corroboration only. They add context but never watchlist a ship on their own, because a detention is about a substandard hull, not a designation.

## The score is a capped sum over 30 days

Two rules keep the score honest:

- **Rolling 30-day window.** Only risk events from the last 30 days count. A ship that cleans up its behaviour decays back down over a month.
- **Per-rule cap of 3x.** Each individual flag can contribute at most three times its single-event weight. So sixteen loitering episodes (30 each) do not add up to 480; they are capped at 3 x 30 = 90. This stops one repeated soft signal from drowning out a single serious one like a sanctions hit.

Formally, a vessel's risk score is the sum, across each distinct flag it has, of `min(total for that flag, 3 x that flag's weight)`. On the map, both the dot size and the glow scale with this number, so the worst vessel in view pulls your eye first.

A worked example. A tanker that is on the OFAC list (100), changed its identity once (40) and loitered eight times in the last month (8 x 30 = 240, capped at 90) scores 100 + 40 + 90 = **230**.

## From score to watchlist

A high number alone does not put a ship on the watchlist. Dark Ships deliberately separates *what is worth showing* from *what is worth trusting*:

- **Suggestions feed.** Everything scoring at or above half the auto-add threshold (25 and up) appears on the public Suggestions page, with its evidence, so you can see what is brewing.
- **Auto watchlist.** A ship is promoted automatically only when its score clears the full threshold (50) **and** it has a hard anchor: either a real sanctions or IUU-list match (not a mere detention), or one of the hard-to-fake deception signals (identity change, cloned MMSI, GPS circle-spoofing, impossible jump, fabricated identity, or a draught change at sea). Soft patterns and detentions only add to the score; they never watchlist a vessel by themselves.

By default the engine runs in a conservative "sanctions-only" mode: only a verified sanctions or IUU-list match promotes a ship automatically, and behaviour-only vessels stay in Suggestions for a human to judge. That keeps the watchlist to vessels you can actually trust are worth watching, and leaves the ambiguous cases for a person to confirm.

## Why it is built this way

Any single signal has an innocent explanation. A ship can lose AIS in a coverage hole; a crew can forget to update the navigation status; two boats can pass close by legitimately. A sanctions listing, by contrast, is a hard fact about a hull. Weighting each flag, summing over a window, capping repeats, and requiring a hard anchor for the watchlist is what turns a noisy stream of individual observations into a signal you can act on, with a low false-positive rate by design.

Want to see it in action? Open the [live map](/) and sort the [Suggestions](/monitor) feed by score, or read the [field guide to vessel flags](/blog/vessel-flags-and-ais-signals) for what each signal means.
