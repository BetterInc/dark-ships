// Single source of truth for turning behaviour-engine rule codes into plain
// language, and for the coarse type buckets used by the Suggestions and Events
// filters. Keep this in sync with the backend PATTERN_LABELS / RULE_LABELS.

export const RULE_LABELS: Record<string, string> = {
  regional_gap: 'went dark in coverage',
  identity_change: 'identity change',
  midsea_appearance: 'appeared mid-sea',
  impossible_jump: 'impossible jump (spoofing)',
  rendezvous: 'possible ship-to-ship',
  drift_mismatch: 'reappeared off predicted drift',
  oil_slick: 'oil slick at transfer point',
  dark_association: 'anchored among sanctioned ships',
  loitering: 'loitering offshore in a trafficking corridor',
  mmsi_collision: 'one identity broadcasting from two places',
  circle_spoofing: 'GPS circle-spoofing (fake track)',
  gps_jamming_zone: 'circular track in an area-jamming zone (likely victim)',
  identity_integrity: 'fabricated identity (bad MMSI/IMO)',
  flag_hop: 'reflagged (one IMO, many MMSIs)',
  nav_status_lie: 'claims anchored while moving',
  gfw_encounter: 'met another vessel at sea (transshipment)',
  gfw_ais_gap: 'disabled AIS (went dark)',
  gfw_loitering: 'loitered offshore',
  draught_change: 'changed draught at sea (possible cargo transfer)',
  risklist_ofac: 'OFAC sanctions list',
  risklist_gur: 'GUR shadow-fleet database',
  risklist_uk: 'UK sanctions list',
  risklist_eu: 'EU sanctions list',
  risklist_canada: 'Canada sanctions list',
  risklist_australia: 'Australia sanctions list',
  risklist_switzerland: 'Switzerland sanctions list',
  risklist_un1718: 'UN (DPRK) sanctions list',
  risklist_parismou: 'banned from EU ports (Paris MoU)',
  risklist_tokyomou: 'detained by port control (Tokyo MoU)',
  risklist_blackseamou: 'detained by port control (Black Sea MoU)',
  risklist_abujamou: 'detained by port control (Abuja MoU)',
  risklist_iuu: 'IUU illegal-fishing blacklist',
  risklist_eu_iuu: 'EU IUU fishing list',
  risklist_uani: 'UANI Iran-tanker list',
  risklist_kse: 'KSE tanker list',
}

export function ruleLabel(rule: string): string {
  return RULE_LABELS[rule] ?? rule.replace(/_/g, ' ')
}

// coarse buckets for the type filter
const TYPE_BUCKETS: Record<string, string[]> = {
  dark: ['regional_gap', 'gfw_ais_gap'],
  sts: ['rendezvous', 'gfw_encounter', 'draught_change', 'oil_slick', 'dark_association'],
  spoof: ['impossible_jump', 'circle_spoofing', 'gps_jamming_zone', 'mmsi_collision',
    'identity_change', 'identity_integrity', 'flag_hop'],
  fishing: ['risklist_iuu', 'risklist_eu_iuu'],
}

export function typeBucket(rule: string): string {
  if (rule === 'risklist_iuu' || rule === 'risklist_eu_iuu') return 'fishing'
  if (rule.startsWith('risklist_')) return 'sanctions'
  for (const [bucket, rules] of Object.entries(TYPE_BUCKETS)) {
    if (rules.includes(rule)) return bucket
  }
  return 'other'
}

export const TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All types' },
  { value: 'sanctions', label: 'Sanctions and lists' },
  { value: 'fishing', label: 'Illegal fishing' },
  { value: 'dark', label: 'Went dark' },
  { value: 'sts', label: 'Ship-to-ship' },
  { value: 'spoof', label: 'Spoofing and identity' },
  { value: 'other', label: 'Other' },
]

/** Pull the most useful human-readable bits out of a rule's detail blob. */
export function detailBits(detail: Record<string, unknown> | null | undefined): string[] {
  if (!detail) return []
  const bits: string[] = []
  const other = detail.other_name ?? detail.other
  if (typeof other === 'string' && other) bits.push(`with ${other}`)
  else if (typeof other === 'number') bits.push(`with MMSI ${other}`)
  if (typeof detail.new_value === 'string' && detail.new_value) bits.push(`now "${detail.new_value}"`)
  if (typeof detail.old_value === 'string' && detail.old_value) bits.push(`was "${detail.old_value}"`)
  if (typeof detail.listed_as === 'string' && detail.listed_as) bits.push(`listed as "${detail.listed_as}"`)
  if (typeof detail.lat === 'number' && typeof detail.lon === 'number') {
    bits.push(`${(detail.lat as number).toFixed(3)}, ${(detail.lon as number).toFixed(3)}`)
  }
  return bits
}
