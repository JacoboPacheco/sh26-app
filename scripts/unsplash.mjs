#!/usr/bin/env node
// Unsplash helper for the /images skill. No dependencies (Node 18+ fetch).
//
//   node scripts/unsplash.mjs status
//     -> {"configured": true|false}, no API call
//   node scripts/unsplash.mjs search "<query>" [--count 6] [--orientation landscape|portrait|squarish]
//     -> saves small thumbnails to .claude/tmp/unsplash/ so Claude can look at them,
//        prints candidates as JSON
//   node scripts/unsplash.mjs use <photo-id>
//     -> sends Unsplash's required "download" ping, appends attribution to CREDITS.md,
//        prints the hotlink URL, alt text, and credit fields for <PhotoCredit>
//   node scripts/unsplash.mjs drop <photo-id>
//     -> removes a swapped-out photo from CREDITS.md (no API call)
//
// Unsplash API rules this follows: images must be hotlinked (never re-hosted),
// choosing a photo must trigger its download_location, photographer + Unsplash
// must be credited. Demo keys allow 50 API requests/hour.

import { mkdirSync, readFileSync, writeFileSync, existsSync, appendFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const API = (process.env.UNSPLASH_API_BASE || 'https://api.unsplash.com').replace(/\/$/, '')
const APP_NAME = 'shellhacks_project'
const THUMB_DIR = join(ROOT, '.claude', 'tmp', 'unsplash')
const CREDITS = join(ROOT, 'CREDITS.md')

function fail(message) {
  console.error(message)
  process.exit(1)
}

function findKey() {
  if (process.env.UNSPLASH_ACCESS_KEY?.trim()) return process.env.UNSPLASH_ACCESS_KEY.trim()
  const envFile = join(ROOT, '.env')
  if (!existsSync(envFile)) return null
  const line = readFileSync(envFile, 'utf8')
    .split(/\r?\n/)
    .find((l) => l.startsWith('UNSPLASH_ACCESS_KEY='))
  return line?.slice('UNSPLASH_ACCESS_KEY='.length).trim() || null
}

function accessKey() {
  const key = findKey()
  if (key) return key
  fail(
    'UNSPLASH_ACCESS_KEY is not set.\n' +
      'Get a free key: https://unsplash.com/developers -> Your apps -> New Application -> copy the Access Key.\n' +
      'Then add this line to .env in the repo root:  UNSPLASH_ACCESS_KEY=your_key_here'
  )
}

async function unsplash(path) {
  const res = await fetch(`${API}${path}`, {
    headers: { Authorization: `Client-ID ${accessKey()}`, 'Accept-Version': 'v1' },
  })
  const remaining = res.headers.get('x-ratelimit-remaining')
  if (res.status === 401) fail('Unsplash rejected the access key (401). Check UNSPLASH_ACCESS_KEY.')
  if (res.status === 403 && remaining === '0') fail('Unsplash hourly rate limit hit (50/hour on demo keys). Try again later.')
  if (!res.ok) fail(`Unsplash request failed: ${res.status} ${await res.text()}`)
  return { data: await res.json(), remaining }
}

const withUtm = (url) => `${url}${url.includes('?') ? '&' : '?'}utm_source=${APP_NAME}&utm_medium=referral`

async function search(query, { count = 6, orientation } = {}) {
  if (!query) fail('Usage: node scripts/unsplash.mjs search "<query>" [--count 6] [--orientation landscape|portrait|squarish]')
  const params = new URLSearchParams({ query, per_page: String(Math.min(Math.max(count, 1), 12)), content_filter: 'high' })
  if (orientation) params.set('orientation', orientation)
  const { data, remaining } = await unsplash(`/search/photos?${params}`)

  mkdirSync(THUMB_DIR, { recursive: true })
  const candidates = []
  for (const photo of data.results) {
    const thumbPath = join(THUMB_DIR, `${photo.id}.jpg`)
    const img = await fetch(photo.urls.thumb)
    if (img.ok) writeFileSync(thumbPath, Buffer.from(await img.arrayBuffer()))
    candidates.push({
      id: photo.id,
      description: photo.alt_description || photo.description || '',
      size: `${photo.width}x${photo.height}`,
      photographer: photo.user?.name,
      thumbnail: img.ok ? thumbPath : null,
      viewUrl: photo.links.html,
    })
  }
  console.log(JSON.stringify({ query, total: data.total, rateLimitRemaining: remaining, candidates }, null, 2))
}

async function use(id) {
  if (!id) fail('Usage: node scripts/unsplash.mjs use <photo-id>')
  const { data: photo } = await unsplash(`/photos/${encodeURIComponent(id)}`)
  const { remaining } = await unsplash(new URL(photo.links.download_location).pathname + new URL(photo.links.download_location).search)

  const alt = photo.alt_description || photo.description || 'Photo from Unsplash'
  const photographerUrl = withUtm(photo.user.links.html)
  const unsplashUrl = withUtm('https://unsplash.com/')
  const credit = `Photo by ${photo.user.name} on Unsplash`

  if (!existsSync(CREDITS)) {
    writeFileSync(CREDITS, '# Credits\n\nThird-party assets used in this project.\n\n## Photos (Unsplash)\n\n')
  }
  const entry = `- ${photo.links.html} — photo by [${photo.user.name}](${photographerUrl}) on [Unsplash](${unsplashUrl})\n`
  if (!readFileSync(CREDITS, 'utf8').includes(photo.links.html)) appendFileSync(CREDITS, entry)

  console.log(
    JSON.stringify(
      {
        id: photo.id,
        hotlinkUrl: photo.urls.regular,
        rawUrlForCustomSize: `${photo.urls.raw}&w=1600&q=80&fit=crop&auto=format`,
        alt,
        credit,
        photographerName: photo.user.name,
        photographerUrl,
        unsplashUrl,
        viewUrl: photo.links.html,
        dominantColor: photo.color,
        rateLimitRemaining: remaining,
      },
      null,
      2
    )
  )
}

function drop(id) {
  if (!id) fail('Usage: node scripts/unsplash.mjs drop <photo-id>')
  if (!existsSync(CREDITS)) return console.log(JSON.stringify({ dropped: false, reason: 'no CREDITS.md' }))
  // page URLs may be http or https and may carry a slug: /photos/a-coffee-cup-<id>
  const safeId = id.replace(/[^A-Za-z0-9_-]/g, '')
  const entry = new RegExp(`^- https?://unsplash\\.com/photos/(?:[^\\s/]*-)?${safeId} `)
  const lines = readFileSync(CREDITS, 'utf8').split('\n')
  const kept = lines.filter((l) => !entry.test(l))
  writeFileSync(CREDITS, kept.join('\n'))
  console.log(JSON.stringify({ dropped: kept.length < lines.length, id }))
}

const [command, ...rest] = process.argv.slice(2)
const flags = {}
const positional = []
for (let i = 0; i < rest.length; i++) {
  if (rest[i] === '--count') flags.count = Number(rest[++i])
  else if (rest[i] === '--orientation') flags.orientation = rest[++i]
  else positional.push(rest[i])
}

if (command === 'status') console.log(JSON.stringify({ configured: Boolean(findKey()) }))
else if (command === 'search') await search(positional.join(' '), flags)
else if (command === 'use') await use(positional[0])
else if (command === 'drop') drop(positional[0])
else fail('Usage: node scripts/unsplash.mjs <search "<query>" | use <photo-id> | drop <photo-id>>')
