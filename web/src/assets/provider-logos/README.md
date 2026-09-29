# Provider SVG assets

`ProviderLogo` discovers `*.svg` and `*.webp` here at build time and inlines them as data URLs, so the page, the SVG chart and exported files all carry the artwork without extra requests. Artwork is shown at 20–28 CSS pixels next to the model name. Third-party access shows the channel followed by the model developer. In dark mode, marks drawn in near-black ink (OpenAI, Anthropic, xAI, Cursor, Factory, Ollama, Meituan) are recoloured to light ink; brands that ship an official dark variant (Kimi, Devin) use a bundled `<slug>-dark.svg` instead; tile logos keep their own background.

## Files

openai, anthropic, claude, xai, cursor, factory, kimi, kimi-dark, zhipu, minimax, alibaba, opencode, deepseek, google, command-code, ollama, xiaomi, tencent, meta, microsoft, meituan, stepfun, devin, devin-dark.

Alibaba uses the Qwen mark; Tencent uses Hunyuan; Meituan uses LongCat; Google uses the official G. Factory (Droid) uses the official Droid Zed mark. Muse Spark uses the Meta mark. Step models use the StepFun five-square mark. Devin (channel) and Cognition (SWE models) both use the official Devin mark. Third-party rows show the reseller first, then the model manufacturer.

## Sources

Most marks are from [lobehub/lobe-icons](https://github.com/lobehub/lobe-icons) (MIT). Xiaomi is from [Simple Icons](https://github.com/simple-icons/simple-icons) (CC0). Factory uses the [Droid Zed mark](https://github.com/Factory-AI/factory-zed-extension/blob/main/droid-zed.svg), recoloured from white to dark ink for light mode. Command Code uses the complete official avatar (rounded frame + ⌘), not a cropped command-only mark. StepFun uses the official five-square mark; the circle gradient is sampled from the current public avatar (lime #67FBB1 → aqua #00F4E5 → cyan #1ACDEE). Chart solid color is the mid aqua #00F4E5. Zhipu Z and the OpenCode window are traced from official rasters. Kimi uses the official Kimi Logomark tile from the Kimi brand kit — the Light variant (black tile) for light theme, the Dark variant (white tile) for dark theme. Devin uses the official Devin mark from the Devin design system — dark-ink and white variants.

Brand marks remain trademarks of their owners. Rebuild recipe: `_assemble.py` (expects a `_fetch/` cache of upstream SVGs).

Prefer SVG assets without embedded rasters or scripts. Command Code intentionally uses its complete avatar, stored as a 96×96 WebP (2 KB; the 400×400 JPEG original was 25 KB). Keep gradient IDs unique within each SVG.
