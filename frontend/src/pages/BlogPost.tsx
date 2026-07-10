import { useEffect } from 'react'
import { Link, useParams } from 'react-router-dom'
import { postBySlug, renderMarkdown } from '../content/posts'

export default function BlogPost() {
  const { slug } = useParams()
  const post = slug ? postBySlug(slug) : undefined

  useEffect(() => {
    if (!post) return
    document.title = `${post.title} · Dark Ships`
    // update the description meta so crawlers that run JS (and in-app previews)
    // see the post's own summary; the worker handles static/social previews.
    const meta = document.querySelector('meta[name="description"]')
    const prev = meta?.getAttribute('content') ?? null
    meta?.setAttribute('content', post.description)
    return () => {
      document.title = 'Dark Ships'
      if (meta && prev !== null) meta.setAttribute('content', prev)
    }
  }, [post])

  if (!post) {
    return (
      <div className="page">
        <p className="empty">Post not found. <Link to="/blog">Back to the blog</Link>.</p>
      </div>
    )
  }

  return (
    <div className="page blog-post">
      <p className="blog-back"><Link to="/blog">← Blog</Link></p>
      <article>
        <h1>{post.title}</h1>
        <div className="blog-meta mono">
          {post.date} · {post.author}{post.tags.length > 0 && ` · ${post.tags.join(' · ')}`}
        </div>
        {/* first-party, in-repo Markdown - trusted content */}
        <div className="blog-body" dangerouslySetInnerHTML={{ __html: renderMarkdown(post.body) }} />
      </article>
    </div>
  )
}
