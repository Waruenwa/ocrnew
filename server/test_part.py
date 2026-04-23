def _partition_boxes(
    candidate_lines: list[str],
    line_boxes: list[tuple[int, int, int, int]],
    page_width: int,
) -> list[list[tuple[int, int, int, int]]]:
    n = len(candidate_lines)
    m = len(line_boxes)
    if n == 0:
        return []
    if n >= m:
        # If more lines than boxes, just distribute as best as possible (some get empty)
        res = []
        for i in range(n):
            if i < m: res.append([line_boxes[i]])
            else: res.append([])
        return res
        
    text_lengths = [len(t.replace(' ', '')) for t in candidate_lines]
    box_widths = [b[2] - b[0] for b in line_boxes]
    
    total_text = sum(text_lengths) or 1
    total_width = sum(box_widths) or 1
    
    # We normalize to make them comparable
    norm_texts = [l / total_text for l in text_lengths]
    norm_boxes = [w / total_width for w in box_widths]
    
    # dp[i][j] = min cost to form i groups using first j boxes
    # choice[i][j] = the starting box index for the i-th group
    import math
    dp = [[math.inf] * (m + 1) for _ in range(n + 1)]
    choice = [[0] * (m + 1) for _ in range(n + 1)]
    
    dp[0][0] = 0
    
    for i in range(1, n + 1):
        target_len = norm_texts[i - 1]
        for j in range(i, m + 1):
            # We can form the i-th group using boxes from k to j-1
            # k must be >= i-1 so that we have enough boxes for the previous groups
            best_cost = math.inf
            best_k = i - 1
            
            for k in range(i - 1, j):
                if dp[i - 1][k] == math.inf:
                    continue
                
                group_width = sum(norm_boxes[k:j])
                # Cost is absolute difference
                cost = dp[i - 1][k] + abs(target_len - group_width)
                
                if cost < best_cost:
                    best_cost = cost
                    best_k = k
            
            dp[i][j] = best_cost
            choice[i][j] = best_k
            
    # Reconstruct
    partitions = []
    curr_j = m
    for i in range(n, 0, -1):
        k = choice[i][curr_j]
        partitions.append(line_boxes[k:curr_j])
        curr_j = k
        
    partitions.reverse()
    return partitions

# Test with page 2 data
texts = [
    "(๓๑พ.)", "สำหรับศาลใช้", "- ๒ -",
    "ข้อ ๒ คดีนี้ (๑) ประกอบราชบัญญัติวิธีพิจารณาคดีผู้บริโภค พ.ศ. ๒๕๕๑ มาตรา ๗ จึงขอ",
    "ให้โจทก์ส่งเอกสารแทนการสืบพยาน",
    "พิเคราะห์คำฟ้องประกอบพยานเอกสารของโจทก์แล้ว ข้อเท็จจริงรับฟังได้ว่า เมื่อวันที่",
    "๒๕ ธันวาคม ๒๕๕๕ จำเลยยื่นคำขอและโจทก์อนุมัติสินเชื่อเฟิร์สช้อยส์โดยออกบัตรกดเงินสดให้",
    "จำเลย ตกลงชำระดอกเบี้ยและค่าธรรมเนียมต่างๆให้โจทก์ตามสัญญา ภายหลังจากทำสัญญา จำเลย",
    "ใช้บัตรกดเงินสดเบิกถอนเงินหลายครั้งแล้วจำเลยผิดสัญญา ก่อนฟ้องคดีนี้โจทก์บอกกล่าวทวงถาม",
    "แล้ว จำเลยเพิกเฉย เห็นว่า เมื่อจำเลยผิดนัดไม่ชำระหนี้ย่อมทำให้โจทก์เสียหายและต้องชำระหนี้คืน",
    "ให้แก่โจทก์ตามสัญญา ส่วนที่โจทก์คิดดอกเบี้ยผิดนัดร้อยละ ๑๕ ต่อปี เป็นอัตราที่เหมาะสมแล้ว",
    "จึงกำหนดให้ตามขอ",
    "พิพากษาให้จำเลยชำระเงิน ๕๕,๓๔๐.๕๒ บาท พร้อมดอกเบี้ยร้อยละ ๑๕ ต่อปี ของต้นเงิน ๗๑,๓๕๓.๒๗ บาท",
    "นับถัดจากวันฟ้อง (ฟ้องวันที่ ๒๖ กันยายน ๒๕๖๗) เป็นต้นไปจนกว่าจะชำระเสร็จแก่โจทก์ กับให้จำเลยใช้ค่าฤชาธรรมเนียมแทนโจทก์ โดยกำหนดค่าทนายความ ๓,๐๐๐ บาท..."
]

boxes = [(0,0,10,0)] * 18  # dummy, doesn't matter much for syntax test
# Actually let's just make boxes have widths roughly reflecting text chunks
box_widths = [len(t) * 10 for t in [
    "(๓๑พ.)", "สำหรับศาลใช้", "- ๒ -",
    "ข้อ ๒ คดีนี้ (๑) ประกอบราชบัญญัติวิธีพิจารณาคดีผู้บริโภค พ.ศ. ๒๕๕๑ มาตรา ๗ จึงขอ",
    "ให้โจทก์ส่งเอกสารแทนการสืบพยาน",
    "พิเคราะห์คำฟ้องประกอบพยานเอกสารของโจทก์แล้ว ข้อเท็จจริงรับฟังได้ว่า เมื่อวันที่",
    "๒๕ ธันวาคม ๒๕๕๕ จำเลยยื่นคำขอและโจทก์อนุมัติสินเชื่อเฟิร์สช้อยส์โดยออกบัตรกดเงินสดให้",
    "จำเลย ตกลงชำระดอกเบี้ยและค่าธรรมเนียมต่างๆให้โจทก์ตามสัญญา ภายหลังจากทำสัญญา จำเลย",
    "ใช้บัตรกดเงินสดเบิกถอนเงินหลายครั้งแล้วจำเลยผิดสัญญา ก่อนฟ้องคดีนี้โจทก์บอกกล่าวทวงถาม",
    "แล้ว จำเลยเพิกเฉย เห็นว่า เมื่อจำเลยผิดนัดไม่ชำระหนี้ย่อมทำให้โจทก์เสียหายและต้องชำระหนี้คืน",
    "ให้แก่โจทก์ตามสัญญา ส่วนที่โจทก์คิดดอกเบี้ยผิดนัดร้อยละ ๑๕ ต่อปี เป็นอัตราที่เหมาะสมแล้ว",
    "จึงกำหนดให้ตามขอ",
    "พิพากษาให้จำเลยชำระเงิน ๕๕,๓๔๐.๕๒ บาท พร้อมดอกเบี้ยร้อยละ ๑๕ ต่อปี",
    "ของต้นเงิน ๗๑,๓๕๓.๒๗ บาท นับถัดจากวันฟ้อง (ฟ้องวันที่ ๒๖ กันยายน ๒๕๖๗) เป็นต้นไป",
    "จนกว่าจะชำระเสร็จแก่โจทก์ กับให้จำเลยใช้ค่าฤชาธรรมเนียมแทนโจทก์ โดยกำหนดค่าทนายความ",
    "๓,๐๐๐ บาท..."
]]
boxes = [(0,0,w,0) for w in box_widths]

print(f"Boxes = {len(boxes)}, Texts = {len(texts)}")
part = _partition_boxes(texts, boxes, 1000)
for i, p in enumerate(part):
    print(f"Text {i:2} ({len(texts[i]):2} chars) gets {len(p)} boxes")
