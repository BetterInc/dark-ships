import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { posts } from '../content/posts'

export default function Blog() {
  useEffect(() => {
    document.title = 'Blog · Dark Ships'
    return () => { document.title = 'Dark Ships' }
  }, [])

  return (
    <div className="page blog-list">
      <h1>Blog</h1>
      <p className="sub">
        Field notes on shadow-fleet detection, maritime OSINT, and the open data
        behind Dark Ships.
      </p>

      <div className="blog-index">
        {posts.map((p) => (
          <article key={p.slug} className="blog-card">
            <h2><Link to={`/blog/${p.slug}`}>{p.title}</Link></h2>
            <div className="blog-meta mono">
              {p.date}{p.tags.length > 0 && ` · ${p.tags.slice(0, 3).join(' · ')}`}
            </div>
            <p>{p.description}</p>
            <Link className="blog-more" to={`/blog/${p.slug}`}>Read →</Link>
          </article>
        ))}
      </div>
    </div>
  )
}
