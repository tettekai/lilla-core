# How long Ollama keeps a model loaded (`llm.providers.<key>.keep_alive`)

> This page is a translation of the [Japanese original](../ja/llm-keep-alive.md). If
> the two differ, the Japanese version is authoritative.

Ollama decides how long a model stays in memory after a call from the request's
`keep_alive` (Ollama's default is 5 minutes). When you use a local LLM for conversation,
a short pause is enough for the model to be unloaded, and the next call has to wait for it
to load again. If you set `keep_alive` on a `type: ollama` entry, every call to that
provider carries `keep_alive` in the request.

```yaml
llm:
  providers:
    local:
      type: ollama
      url: http://localhost:11434
      model: example-model
      keep_alive: 30m
```

## Writing it

- The value is a string (`30m`, `1h`, ...) or an integer (seconds). The integer `-1`
  keeps the model loaded indefinitely, and `0` unloads it right after the call.
- The value is sent as written. lilla-core does not validate the string format (`30m`
  etc.); an invalid format becomes an error from Ollama.
- Values that are neither a string nor an integer, such as `true` / `false`, decimals or
  lists, fail startup with a `ValidationError`.
- If you omit it, nothing is sent (Ollama's default applies, as before).

## Where it goes

It is added at the top level of the request body, separately from `extra_params`.

- Calls without tools (`/api/chat`): `extra_params` goes inside `options`, but
  `keep_alive` is not put in `options`; it is added at the top level (writing
  `keep_alive` in `extra_params` puts it in `options`, where Ollama ignores it).
- Calls with tools (Ollama's OpenAI-compatible endpoint `/v1/chat/completions`): also
  added at the top level.

```json
{
  "model": "example-model",
  "messages": [{"role": "user", "content": "Hello"}],
  "stream": false,
  "options": {"temperature": 0.7},
  "keep_alive": "30m"
}
```

## Setting it on a non-Ollama provider

Only `type: ollama` entries may set `keep_alive`. Setting it on a `type: openai_compat`
or `type: resolver` entry fails startup with a `ValidationError`. To apply it to turns
that go through a resolver, set it on each Ollama entry the resolver can choose (see
[Choosing the LLM provider per turn](llm-resolver.md)).
