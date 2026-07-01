from __future__ import annotations

from typing import Final

HOST_PROXY_SCRIPT: Final = r'''const fs = require("fs")
const http = require("http")
const net = require("net")

const upstreamPort = Number(process.argv[2])
const bindHost = process.argv[3]
const portFile = process.argv[4]
const upstreamHost = "127.0.0.1"

const proxyHeaders = (headers, { upgrade = false } = {}) => {
  const result = { ...headers, host: `${upstreamHost}:${upstreamPort}` }
  if (upgrade) {
    result.connection = headers.connection || "Upgrade"
  } else {
    result.connection = "close"
  }
  return result
}

const server = http.createServer((req, res) => {
  const upstream = http.request(
    {
      host: upstreamHost,
      port: upstreamPort,
      path: req.url,
      method: req.method,
      headers: proxyHeaders(req.headers),
      agent: false,
    },
    (upstreamRes) => {
      const headers = { ...upstreamRes.headers }
      delete headers.connection
      delete headers["keep-alive"]
      res.writeHead(upstreamRes.statusCode || 502, headers)
      upstreamRes.pipe(res)
    },
  )

  upstream.on("error", (error) => {
    res.statusCode = 502
    res.end(String(error))
  })

  req.pipe(upstream)
})

server.on("upgrade", (req, socket, head) => {
  const upstream = net.connect({ host: upstreamHost, port: upstreamPort }, () => {
    const lines = [`${req.method} ${req.url} HTTP/${req.httpVersion}`]
    const headers = proxyHeaders(req.headers, { upgrade: true })
    for (const [name, value] of Object.entries(headers)) {
      if (Array.isArray(value)) {
        for (const item of value) lines.push(`${name}: ${item}`)
      } else if (value !== undefined) {
        lines.push(`${name}: ${value}`)
      }
    }
    upstream.write(`${lines.join("\r\n")}\r\n\r\n`)
    if (head.length > 0) upstream.write(head)
    socket.pipe(upstream).pipe(socket)
  })

  upstream.on("error", () => socket.destroy())
  socket.on("error", () => upstream.destroy())
})

server.listen(0, bindHost, () => {
  fs.writeFileSync(portFile, String(server.address().port))
})
'''
