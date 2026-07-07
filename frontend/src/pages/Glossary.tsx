// Plain-language reference for every flag the behaviour engine can raise, plus
// a short FAQ. Public page - linked from the ship evidence panels ("what do
// these mean?") and the nav.

interface Flag {
  rule: string
  name: string
  what: string
  why: string
}

interface Group {
  key: string
  title: string
  intro: string
  flags: Flag[]
}

const GROUPS: Group[] = [
  {
    key: 'sanctions',
    title: 'Sanctions and official lists',
    intro:
      'A government or international body has named this exact hull. These are the ' +
      'hardest, most trustworthy signals - a designation, not a guess - and on their ' +
      'own they are enough to put a vessel on the watchlist.',
    flags: [
      { rule: 'risklist_ofac', name: 'OFAC sanctions list', what: 'On the US Treasury (OFAC) Specially Designated Nationals list.', why: 'A direct US sanctions designation - the strongest single signal we carry.' },
      { rule: 'risklist_uk', name: 'UK sanctions list', what: 'On the UK FCDO sanctions register.', why: 'A formal UK designation, often mirroring Russia and DPRK measures.' },
      { rule: 'risklist_eu', name: 'EU sanctions list', what: 'Named in the EU Annex XLII port-ban / sanctions package.', why: 'An EU designation; banned or restricted across member states.' },
      { rule: 'risklist_gur', name: 'GUR shadow-fleet database', what: 'Listed in Ukraine military intelligence (GUR) shadow-fleet data.', why: 'The largest structured shadow-fleet list; tracks sanctions-evading tankers.' },
      { rule: 'risklist_un1718', name: 'UN (DPRK) sanctions list', what: 'On the UN Security Council 1718 North Korea list.', why: 'A UN designation tied to DPRK sanctions evasion.' },
      { rule: 'risklist_uani', name: 'UANI Iran-tanker list', what: 'On United Against Nuclear Iran’s "Ghost Armada" tanker tracker.', why: 'Flags tankers moving sanctioned Iranian oil.' },
      { rule: 'risklist_canada', name: 'Canada / Australia / Switzerland lists', what: 'On the Canadian SEMA, Australian DFAT or Swiss SECO sanctions lists.', why: 'Allied designations that add net-new hulls beyond the US/EU/UK lists.' },
      { rule: 'risklist_parismou', name: 'Banned from EU ports (Paris MoU)', what: 'On the Paris MoU port-state-control banning list.', why: 'Refused access to European ports - strong, though not a full sanction.' },
    ],
  },
  {
    key: 'detentions',
    title: 'Port-control detentions (corroboration only)',
    intro:
      'A port inspection detained the ship. That means substandard or poorly ' +
      'maintained - not proof of a crime, and there are thousands - so a detention ' +
      'never puts a vessel on the watchlist by itself. It only strengthens the case ' +
      'when the ship is also flagged for something else.',
    flags: [
      { rule: 'risklist_tokyomou', name: 'Detained by port control (Tokyo MoU)', what: 'Detained during a Tokyo MoU port-state-control inspection (Asia-Pacific).', why: 'A substandard-ship signal that corroborates other flags.' },
      { rule: 'risklist_blackseamou', name: 'Detained by port control (Black Sea MoU)', what: 'Detained in Black / Azov Sea shadow-fleet waters.', why: 'Corroboration in a region dense with sanctions evasion.' },
      { rule: 'risklist_abujamou', name: 'Detained by port control (Abuja MoU)', what: 'Detained in West / Central Africa (Abuja MoU).', why: 'Corroboration in narco, oil-theft and IUU-fishing waters.' },
    ],
  },
  {
    key: 'fishing',
    title: 'Illegal fishing',
    intro: 'Named on an official blacklist of vessels caught fishing illegally.',
    flags: [
      { rule: 'risklist_iuu', name: 'IUU illegal-fishing blacklist', what: 'On a combined RFMO blacklist (CCAMLR, ICCAT, and others).', why: 'Documented illegal, unreported or unregulated fishing.' },
      { rule: 'risklist_eu_iuu', name: 'EU IUU fishing list', what: 'On the EU’s adopted IUU vessel list (official EU law).', why: 'The EU-legal version of the fishing blacklists.' },
    ],
  },
  {
    key: 'dark',
    title: 'Going dark',
    intro:
      'The ship stopped transmitting AIS when we would expect to hear it. Going ' +
      'dark is the classic move before a covert transfer or a sanctioned port call.',
    flags: [
      { rule: 'regional_gap', name: 'Went dark in coverage', what: 'Fell silent while underway inside solid receiver coverage, away from the edge of range.', why: 'A ship that switches off its transponder mid-voyage is usually hiding a movement.' },
      { rule: 'gfw_ais_gap', name: 'Disabled AIS (went dark)', what: 'Global Fishing Watch recorded an intentional AIS gap.', why: 'Independent confirmation the vessel stopped broadcasting.' },
    ],
  },
  {
    key: 'sts',
    title: 'Ship-to-ship transfer',
    intro:
      'Signs of two vessels meeting at sea to move cargo - the core method for ' +
      'laundering sanctioned oil and moving contraband away from port oversight.',
    flags: [
      { rule: 'rendezvous', name: 'Possible ship-to-ship', what: 'Two tankers sat within a few hundred metres of each other, near-stationary and offshore, both recently underway.', why: 'The geometry of an at-sea cargo transfer.' },
      { rule: 'gfw_encounter', name: 'Met another vessel at sea', what: 'Global Fishing Watch logged an encounter (transshipment) event.', why: 'Independent confirmation of an at-sea meeting.' },
      { rule: 'draught_change', name: 'Changed draught at sea', what: 'The ship’s reported draught (how deep it sits) changed near a known transfer zone.', why: 'It loaded or unloaded cargo while offshore, not at a port.' },
      { rule: 'oil_slick', name: 'Oil slick at transfer point', what: 'Satellite radar saw an oil slick at the meeting location.', why: 'Near-conclusive physical evidence of an oil transfer.' },
      { rule: 'dark_association', name: 'Anchored among sanctioned ships', what: 'Sat at anchor in a cluster of sanctioned or shadow-fleet vessels.', why: 'Company kept: sanctioned fleets gather in the same transfer anchorages.' },
    ],
  },
  {
    key: 'spoof',
    title: 'Spoofing and identity',
    intro:
      'The ship is faking its position or its identity. These are deliberate ' +
      'deceptions - a strong signal something is being hidden - and several are ' +
      'enough to flag a vessel on their own.',
    flags: [
      { rule: 'identity_change', name: 'Identity change', what: 'Broadcast a different name, IMO or callsign than before.', why: 'Swapping identity is the classic way to shed a sanctioned history.' },
      { rule: 'mmsi_collision', name: 'One identity in two places', what: 'The same MMSI transmitted two separate tracks at the same time.', why: 'A cloned identity - two hulls hiding behind one legitimate ID.' },
      { rule: 'circle_spoofing', name: 'GPS circle-spoofing', what: 'Broadcast a fake, near-perfect circular track while claiming to move.', why: 'A hallmark of GNSS manipulation to mask the real location.' },
      { rule: 'impossible_jump', name: 'Impossible jump', what: 'Consecutive positions that imply an impossible speed (over 40 knots).', why: 'The track was tampered with - a spoofed or injected position.' },
      { rule: 'identity_integrity', name: 'Fabricated identity', what: 'Broadcast an invalid or factory-default MMSI / bad country prefix.', why: 'A made-up identity - a core identity-laundering signature.' },
      { rule: 'flag_hop', name: 'Reflagged repeatedly', what: 'One hull (IMO) seen under many different MMSIs / flags.', why: 'Rapid reflagging is used to stay ahead of sanctions and inspections.' },
      { rule: 'nav_status_lie', name: 'Claims anchored while moving', what: 'Declared "at anchor" or "moored" while clearly under way.', why: 'Can be a stale crew setting, but also a way to look innocent to filters.' },
    ],
  },
  {
    key: 'other',
    title: 'Other behaviour',
    intro:
      'Softer patterns. On their own they are weak - they corroborate rather than ' +
      'accuse - but stacked with a hard signal they raise the risk score.',
    flags: [
      { rule: 'loitering', name: 'Loitering offshore', what: 'Held position, near-stationary, well offshore in a known trafficking corridor.', why: 'Waiting offshore is consistent with a hand-off or a covert meeting.' },
      { rule: 'gfw_loitering', name: 'Loitered offshore (GFW)', what: 'Global Fishing Watch recorded a loitering event.', why: 'Independent corroboration of offshore dwelling.' },
      { rule: 'midsea_appearance', name: 'Appeared mid-sea', what: 'A brand-new identity first seen in open water rather than leaving a port.', why: 'Can simply be where we first received it - weak on its own, used only to corroborate.' },
      { rule: 'drift_mismatch', name: 'Reappeared off predicted drift', what: 'After going dark, reappeared far from where currents would have carried it.', why: 'Suggests it was under power in the gap, not just out of range.' },
    ],
  },
]

const FAQ: { q: string; a: string }[] = [
  {
    q: 'What does "flagged - not on watchlist" mean?',
    a: 'The engine has seen something suspicious, but not yet a hard enough anchor (a sanctions designation or a deliberate deception) to add it automatically. It is a lead to review, not a confirmed case.',
  },
  {
    q: 'How does a ship get on the watchlist?',
    a: 'It needs a hard anchor: either a government sanctions / fishing designation, or a hard behavioural signal (identity change, cloned identity, GPS spoofing, impossible jump, fabricated identity, or a draught change at sea). Soft patterns and port detentions only add to the score - they never put a ship on the list by themselves.',
  },
  {
    q: 'Are these confirmed crimes?',
    a: 'No. Flags are signals, not verdicts. Some (a sanctions match) are hard facts about the hull; others (loitering, going dark) are behaviours that have innocent explanations too. The risk score reflects how much the evidence stacks up, and the tools are meant to help a human decide what to look at.',
  },
  {
    q: 'What is the risk score?',
    a: 'Each flag carries a weight. The score is the sum of a vessel’s flags over the last 30 days, so a ship with several corroborating signals scores higher than one with a single weak one.',
  },
  {
    q: 'Where does the data come from?',
    a: 'Live AIS worldwide, government and NGO sanctions / fishing / detention lists, Global Fishing Watch behavioural events, and Sentinel-1/2 satellite imagery for cross-checks. Every source is free and listed on the Sources page.',
  },
]

export default function Glossary() {
  return (
    <div className="page glossary">
      <h1>Flag glossary</h1>
      <p className="sub">
        Every signal the system can raise, in plain language: what it means and why it
        matters. Flags are leads, not verdicts - see the FAQ at the bottom.
      </p>

      {GROUPS.map((g) => (
        <section key={g.key} className="gloss-group">
          <h2>{g.title}</h2>
          <p className="gloss-intro">{g.intro}</p>
          <div className="gloss-grid">
            {g.flags.map((f) => (
              <div key={f.rule} className="gloss-card">
                <div className="gloss-name">{f.name}</div>
                <div className="gloss-what">{f.what}</div>
                <div className="gloss-why"><span>Why it matters</span> {f.why}</div>
              </div>
            ))}
          </div>
        </section>
      ))}

      <section className="gloss-group">
        <h2>FAQ</h2>
        <div className="gloss-faq">
          {FAQ.map((item) => (
            <div key={item.q} className="gloss-faq-item">
              <div className="gloss-q">{item.q}</div>
              <div className="gloss-a">{item.a}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}
