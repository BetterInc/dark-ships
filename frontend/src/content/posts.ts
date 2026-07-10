import { marked } from 'marked'

// Blog posts are authored as Markdown files with YAML-ish frontmatter under
// ./blog/*.md and bundled at build time via Vite's import.meta.glob. Content is
// first-party (in-repo, written by us), so the rendered HTML is trusted.

export interface Post {
  slug: string
  title: string
  description: string
  date: string
  tags: string[]
  author: string
  body: string
}

function parseFrontmatter(raw: string): { meta: Record<string, string | string[]>; body: string } {
  const m = raw.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?([\s\S]*)$/)
  if (!m) return { meta: {}, body: raw }
  const meta: Record<string, string | string[]> = {}
  for (const line of m[1].split(/\r?\n/)) {
    const idx = line.indexOf(':')
    if (idx === -1) continue
    const key = line.slice(0, idx).trim()
    const val = line.slice(idx + 1).trim()
    if (val.startsWith('[') && val.endsWith(']')) {
      meta[key] = val.slice(1, -1).split(',')
        .map((s) => s.trim().replace(/^["']|["']$/g, '')).filter(Boolean)
    } else {
      meta[key] = val.replace(/^["']|["']$/g, '')
    }
  }
  return { meta, body: m[2] }
}

const raws = import.meta.glob('./blog/*.md', {
  query: '?raw', import: 'default', eager: true,
}) as Record<string, string>

export const posts: Post[] = Object.entries(raws)
  .map(([path, raw]) => {
    const { meta, body } = parseFrontmatter(raw)
    const slug = (meta.slug as string) || path.split('/').pop()!.replace(/\.md$/, '')
    return {
      slug,
      title: (meta.title as string) || slug,
      description: (meta.description as string) || '',
      date: (meta.date as string) || '',
      tags: Array.isArray(meta.tags) ? meta.tags : [],
      author: (meta.author as string) || 'Dark Ships',
      body,
    }
  })
  // newest first
  .sort((a, b) => (a.date < b.date ? 1 : -1))

export function postBySlug(slug: string): Post | undefined {
  return posts.find((p) => p.slug === slug)
}

export function renderMarkdown(md: string): string {
  return marked.parse(md, { async: false }) as string
}
