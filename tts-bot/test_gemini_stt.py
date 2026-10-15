import asyncio, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, '.')

import bot

async def main():
    # Test audio faylini o'qish
    test_file = 'test_uz.mp3'
    try:
        with open(test_file, 'rb') as f:
            audio_bytes = f.read()
        print(f'Audio fayl: {test_file} ({len(audio_bytes)} bytes)')
        
        # Gemini STT chaqirish
        result = await bot.gemini_stt(audio_bytes, 'audio/mpeg')
        print(f'STT natija: {result}')
        
    except FileNotFoundError:
        print(f'{test_file} topilmadi')
    except Exception as e:
        print(f'Xato: {e}')

asyncio.run(main())
