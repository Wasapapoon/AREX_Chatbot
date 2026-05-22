import os
import json
import requests
from fastapi import FastAPI, Request, HTTPException
from openai import OpenAI
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

app = FastAPI()

# 1. ตั้งค่า API Key ต่างๆ (ใส่คีย์จริงของคุณตรงนี้ได้เลย)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
LINE_CHANNEL_ACCESS_TOKEN = "ow2L0VaoMgZyhKJuO7z8JfILFRT8LLyCw6WtsSAvngZZ2HJUpca8XsEG+7mLewnFYp4j+qLuYRFtzhs9oRhKVeelqhWLmTlx3L8xjYkIh3XimTt8u8sWzYT3T8mcxwLoX/Zwvd0ftFkdCh0zsTmSwQdB04t89/1O/w1cDnyilFU="
LINE_CHANNEL_SECRET = "60400081b3e6a9c10ceb19f47efa28f7"
LOVABLE_API_URL = "https://arexhotel.lovable.app/api/public/rooms/book"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# ถังเก็บประวัติแชทแยกตามรายบุคคล (ใช้ User ID ของ LINE เป็นคีย์)
user_session_history = {}

SYSTEM_INSTRUCTION = """
คุณคือ AI Agent หน้าที่คือช่วยลูกค้าจองห้องพัก โดยต้องเก็บข้อมูล 3 อย่างให้ครบ:
1. ชื่อเล่นหรือชื่อจริงลูกค้า เก็บไว้ในตัวแปร <name>
2. ชั้นที่ต้องการ (1-7) สกัดออกมาเป็นตัวเลขแล้วเก็บไว้ในตัวแปร <floor>
3. เลขห้องที่ต้องการ (1-15) สกัดออกมาเป็นตัวเลขแล้วเก็บไว้ในตัวแปร <room_number>

กฎเหล็กการทำงาน:
- ชวนคุยด้วยภาษาไทยที่เป็นมิตร หากข้อมูลยังไม่ครบ ให้ถามเอาข้อมูลที่ขาดทีละอย่าง
- หากลูกค้าพิมพ์รวบยอดมา (เช่น "ชื่อเต้ ชั้น 6 ห้อง 09") ให้ดึงข้อมูลมาให้หมด
- เมื่อใดก็ตามที่ได้ข้อมูลครบทั้ง 3 อย่างแล้ว ให้จบการสนทนาทันที และห้ามตอบข้อความธรรมดา แต่ให้พิมพ์ผลลัพธ์ออกมาในรูปแบบ JSON Object รูปแบบนี้รูปแบบเดียวเท่านั้น:
{
  "status": "complete",
  "name": "ชื่อที่สกัดได้",
  "floor": ตัวเลขชั้นที่เป็น Integer,
  "room_number": ตัวเลขห้องที่เป็น Integer
}
"""

# ท่อรับ Webhook จาก LINE OA
@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

# ฟังก์ชันจัดการเมื่อมีคนส่งข้อความข้อความตัวอักษรเข้ามาใน LINE
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    user_input = event.message.text.strip()
    
    # 2. จัดการประวัติการแชท (ถ้าเป็นคนใหม่ ให้สร้างถังเก็บใหม่)
    if user_id not in user_session_history or user_input in ["ล้างแชท", "เริ่มใหม่", "จองใหม่"]:
        user_session_history[user_id] = []
        
    history = user_session_history[user_id]
    
    # เตรียม Messages สำหรับส่งให้ OpenAI
    openai_messages = [{"role": "system", "content": SYSTEM_INSTRUCTION}]
    for msg in history:
        openai_messages.append(msg)
    openai_messages.append({"role": "user", "content": user_input})
    
    # 3. เรียกใช้งาน GPT-4o-mini
    response = client.chat.completions.create(
        model='gpt-4o-mini',
        messages=openai_messages,
        temperature=0.0
    )
    
    ai_reply = response.choices[0].message.content.strip()
    
    # 4. ตรวจสอบสภาวะการสกัดข้อมูลสมบูรณ์
    if "{" in ai_reply and "}" in ai_reply and '"status": "complete"' in ai_reply:
        try:
            json_str = ai_reply[ai_reply.find("{"):ai_reply.rfind("}")+1]
            data = json.loads(json_str)
            
            payload = {
                "nickname": data["name"],
                "floor": int(data["floor"]),
                "room": int(data["room_number"])
            }
            
            if payload["floor"] > 7 or payload["room"] > 15:
                bot_response = f"❌ ขออภัยค่ะ ชั้น {payload['floor']} หรือห้อง {payload['room']} ไม่มีอยู่ในระบบ (รองรับชั้น 1-7 และห้อง 1-15) กรุณาพิมพ์ 'เริ่มใหม่' เพื่อจองอีกครั้ง"
            else:
                # ยิงตรงเข้า Lovable หลังบ้าน
                res = requests.post(LOVABLE_API_URL, json=payload, timeout=10)
                res_data = res.json()
                
                if res.status_code == 200 and res_data.get("success"):
                    bot_response = f"🎉 บันทึกการจองห้อง {payload['room']} ชั้น {payload['floor']} ให้คุณ {payload['nickname']} เรียบร้อยแล้วค่ะ!"
                    # เคลียร์ประวัติหลังจองสำเร็จเพื่อไม่ให้ค้างลูป
                    user_session_history[user_id] = []
                else:
                    bot_response = "❌ ระบบหลังบ้านเกิดข้อผิดพลาด ไม่สามารถบันทึกการจองได้"
        except Exception:
            bot_response = "⚠️ เกิดข้อผิดพลาดในการดึงข้อมูล กรุณาลองใหมู่อีกครั้ง"
    else:
        # ถ้าข้อมูลยังไม่ครบ ให้อัปเดตประวัติการคุยปกติ
        bot_response = ai_reply
        user_session_history[user_id].append({"role": "user", "content": user_input})
        user_session_history[user_id].append({"role": "assistant", "content": ai_reply})
        
    # ส่งข้อความตอบกลับไปหาลูกค้าบน LINE
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=bot_response))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)