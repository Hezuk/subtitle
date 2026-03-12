#!/usr/bin/env python3
import anthropic
import re
import sys

def parse_srt(content):
    blocks = []
    pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3} --> \d{2}:\d{2}:\d{2},\d{3})\n([\s\S]*?)(?=\n\n\d+\n|\Z)'
    matches = re.findall(pattern, content.strip())
    for idx, timestamp, text in matches:
        blocks.append({
            'idx': idx,
            'timestamp': timestamp,
            'text': text.strip()
        })
    return blocks

def translate_batch(client, blocks):
    korean_texts = '\n---\n'.join([f"[{b['idx']}] {b['text']}" for b in blocks])

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{
            "role": "user",
            "content": f"""Translate the following Korean subtitle segments to English.
Rules:
- Each segment is marked with [number]
- Write complete, natural English sentences (not word-for-word translation)
- Keep the same [number] markers
- Keep translations concise to fit subtitle timing
- Return ONLY the translated segments in the same format

{korean_texts}"""
        }]
    )

    response = message.content[0].text

    # Parse response back
    translated = {}
    for block in blocks:
        pattern = rf'\[{block["idx"]}\]\s*(.*?)(?=\n\[|\Z)'
        match = re.search(pattern, response, re.DOTALL)
        if match:
            translated[block['idx']] = match.group(1).strip()
        else:
            translated[block['idx']] = block['text']  # fallback to original

    return translated

def main():
    input_file = '1장.srt'
    output_file = '1장_english.srt'

    with open(input_file, 'r', encoding='utf-8') as f:
        content = f.read()

    blocks = parse_srt(content)
    print(f"Total subtitle blocks: {len(blocks)}")

    client = anthropic.Anthropic()

    # Process in batches of 20
    batch_size = 20
    all_translated = {}

    for i in range(0, len(blocks), batch_size):
        batch = blocks[i:i+batch_size]
        print(f"Translating blocks {i+1}-{min(i+batch_size, len(blocks))}...")
        translated = translate_batch(client, batch)
        all_translated.update(translated)

    # Write output SRT
    output_lines = []
    for block in blocks:
        translated_text = all_translated.get(block['idx'], block['text'])
        output_lines.append(f"{block['idx']}\n{block['timestamp']}\n{translated_text}\n")

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(output_lines))

    print(f"Translation complete: {output_file}")

if __name__ == '__main__':
    main()
