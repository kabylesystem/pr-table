# Prompts

Everything visual in PR Table that is not CSS was generated with the OpenAI image API. The exact prompts live in `scripts/prompts.json`, and `scripts/gen_images.py` reproduces them.

## Model and settings

- Model: `gpt-image-2.6-sunburst` (override with `IMAGE_MODEL`). Pick a model that supports `background: "transparent"`, otherwise the figures come back on a solid background.
- Figures: `size 1024x1536`, `quality high`, `background transparent`, `output_format png`. The script then crops each figure to its outline.
- Launch images: `size 1536x1024`, `quality high`.

## Crew figures

Base prompt (the `{desc}` part changes per figure):

> A single brick-style plastic minifigure toy of a fictional software developer, full body from head to feet, standing upright, facing the camera with a slight three-quarter turn, arms relaxed, glossy ABS plastic with crisp studio lighting and soft reflections, high-end toy product photography, sharp focus, centered, the whole figure visible, isolated on a fully transparent background, no text, no logos, no brand marks, no base plate. Character: {desc}.

The four default characters are in `scripts/prompts.json`. Two rules make the set look consistent: keep the base prompt identical, and change only the hair, the clothes and one prop.

## Your teammates as figures

Use the image edit endpoint with a photo and the same base prompt:

```bash
curl https://api.openai.com/v1/images/edits \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -F model=gpt-image-2.6-sunburst \
  -F image=@photo.jpg \
  -F background=transparent \
  -F size=1024x1536 \
  -F prompt="Turn this person into a single brick-style plastic minifigure toy, full body from head to feet, standing upright, slight three-quarter turn, keep their hairstyle, facial hair, glasses and outfit colors, glossy ABS plastic, crisp studio lighting, isolated on a fully transparent background, no text, no logos." \
  | python3 -c 'import base64,json,sys;open("me.png","wb").write(base64.b64decode(json.load(sys.stdin)["data"][0]["b64_json"]))'
```

Then drop `me.png` into `web/assets/crew/` and list it in `config.json` under `crew` (and `reviewer.figure` for your reviewer). Get consent before you put someone's face on a screen.

## Logo and cover

The logo and the Product Hunt cover are HTML, not generated images, so the lettering stays crisp: `launch/logo.html` and `launch/cover.html`. Render them with any headless browser at 2x. The title style is Bungee, skewed by -9°, with a stacked text shadow from `#8fcc2f` to `#426210` for the 3D edge.
