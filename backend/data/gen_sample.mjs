// Sinh data/sample/* (dữ liệu TỰ SINH, chỉ để chạy thông code — không phải bộ đánh giá).
// Chạy: node scripts/gen-sample.mjs
import { writeFileSync, mkdirSync } from "node:fs";
import path from "node:path";

const dir = path.join(process.cwd(), "data", "sample");
mkdirSync(dir, { recursive: true });

const U = {
  hung: { user_id: "u_hung", display_name: "Hưng", aliases: ["anh Hưng", "Hung"] },
  linh: { user_id: "u_linh", display_name: "Linh", aliases: ["Linh Nguyễn"] },
  nam: { user_id: "u_nam", display_name: "Nam", aliases: ["Nam Trần"] },
  mai: { user_id: "u_mai", display_name: "Mai", aliases: ["Mai Phạm"] },
  tuan: { user_id: "u_tuan", display_name: "Tuấn", aliases: ["Tuan"] },
};
const members = {
  ch_design: [U.hung, U.linh, U.nam, U.mai, U.tuan],
  dm_hung_linh: [U.hung, U.linh],
};

const T = (d, hms) => `2026-11-${d}T${hms.length === 5 ? hms + ":00" : hms}+07:00`;
const msgs = [];
function m(id, conv, d, hms, who, text, extra = {}) {
  const u = U[who];
  msgs.push({
    id,
    conversation_id: conv,
    conversation_type: conv.startsWith("dm_") ? "dm" : "group",
    thread_root_id: null,
    created_at: T(d, hms),
    author_id: u.user_id,
    author_name: u.display_name,
    text,
    mentions: (extra.mentions ?? []).map((k) => ({ user_id: U[k].user_id, name: U[k].display_name })),
    reply_to_id: extra.reply ?? null,
    reactions: (extra.reactions ?? []).map(([k, type, at]) => ({ user_id: U[k].user_id, type, created_at: T(d, at) })),
    ...(extra.files ? { attachments: extra.files } : {}),
  });
}
const G = "ch_design";
const DM = "dm_hung_linh";

// ---------- Thứ 2, 09/11 ----------
m("m001", G, "09", "08:58", "hung", "Chào cả nhà, tuần này team focus vào màn Checkout nha");
m("m002", G, "09", "08:59", "linh", "Dạ vâng ạ");
m("m003", G, "09", "09:05", "hung", "@Linh gửi báo cáo tuần cho anh trước thứ 5 nhé", { mentions: ["linh"], reactions: [["linh", "like", "09:06:30"]] });
m("m004", G, "09", "09:06", "linh", "Dạ ok anh", { reply: "m003" });
m("m005", G, "09", "09:10", "nam", "Hôm qua em test lại flow thanh toán, còn 2 bug nhỏ ở bước nhập mã giảm giá");
m("m006", G, "09", "09:11", "nam", "File log đây ạ", { files: ["bug_report_checkout.xlsx"] });
m("m007", G, "09", "09:15", "hung", "Mình cần cập nhật lại tài liệu API cho module voucher");
m("m008", G, "09", "09:16", "tuan", "Doc cũ viết từ hồi v1 nên lệch nhiều lắm");
m("m009", G, "09", "09:20", "mai", "Để em làm cho ạ");
m("m010", G, "09", "09:21", "hung", "ok Mai, thứ 6 xong nhé");
m("m011", G, "09", "09:40", "tuan", "Mọi người ơi, mình nên làm hướng nào cho phần animation, Lottie hay CSS?");
m("m012", G, "09", "09:42", "nam", "CSS cho nhẹ anh, Lottie nặng bundle lắm");
m("m013", G, "09", "09:43", "hung", "Để anh xem đã");
m("m014", G, "09", "10:30", "hung", "@Nam fix 2 bug mã giảm giá, @Tuấn làm lại icon set cho màn Checkout", { mentions: ["nam", "tuan"], reactions: [["tuan", "like", "10:33"]] });
m("m015", G, "09", "10:31", "nam", "dc a", { reply: "m014" });
m("m016", G, "09", "11:00", "linh", "Chị Mai ơi spacing giữa các card là 16 hay 24 ạ?");
m("m017", G, "09", "11:02", "mai", "24 em nhé, theo grid mới");
m("m018", G, "09", "11:30", "tuan", "Ai rảnh review giúp mình PR #482 không?");
m("m019", G, "09", "11:31", "tuan", "PR này đổi lại layout của bước xác nhận đơn");
m("m020", G, "09", "12:05", "linh", "Trưa nay cả team ăn bún chả không mọi người");
m("m021", G, "09", "12:06", "nam", "Đi đi chị");
m("m022", G, "09", "13:45", "nam", "để em review cho", { reply: "m018" });
m("m023", G, "09", "14:00", "hung", "em làm giúp anh cái slide tổng kết sprint nhé");
m("m024", G, "09", "14:02", "linh", "dạ em làm ạ", { reply: "m023" });
m("m025", G, "09", "14:03", "hung", "slide xong trước buổi sprint review nhé");
m("m026", G, "09", "15:00", "mai", "Mai em gửi anh bản mockup cuối nhé");
m("m027", G, "09", "15:30", "tuan", "Bên brand vừa share guideline màu mới, mọi người tham khảo", { files: ["brand_guideline_v2.pdf"] });

// DM
m("d001", DM, "09", "16:00", "hung", "em làm giúp anh bảng so sánh giá 3 nhà cung cấp nhé, mai gửi anh");
m("d002", DM, "09", "16:02", "linh", "Dạ vâng anh");

// ---------- Thứ 3, 10/11 ----------
m("m028", G, "10", "08:50", "linh", "Chào buổi sáng mọi người");
m("m029", G, "10", "09:05", "mai", "Mockup em up lên Figma rồi ạ", { files: ["checkout_mockup_final.fig"] });
m("d003", DM, "10", "09:00", "hung", "em nhớ làm bảng so sánh giá nhà cung cấp nhé");
m("d004", DM, "10", "09:01", "linh", "Dạ em gửi rồi ạ, anh check mail nhé");
m("m030", G, "10", "09:30", "hung", "@Linh báo cáo tuần lùi sang thứ 6 nhé, anh cần thêm số liệu tháng", { mentions: ["linh"] });
m("m031", G, "10", "09:31", "linh", "dạ vâng anh");
m("m032", G, "10", "10:00", "hung", "à thôi thứ 7 nhé, sáng thứ 7 anh mới đọc");
m("m033", G, "10", "10:30", "nam", "Bug mã giảm giá em fix xong rồi nhé");
m("m034", G, "10", "10:45", "tuan", "Icon set em làm xong 70% rồi");
m("m035", G, "10", "11:00", "hung", "@Tuấn thôi khỏi làm icon set nhé, team brand gửi bộ mới rồi", { mentions: ["tuan"] });
m("m036", G, "10", "13:00", "hung", "Linh với Nam chuẩn bị slide demo cho khách hàng thứ 5 tuần sau nhé", { reactions: [["nam", "like", "13:05"]] });
m("m037", G, "10", "13:06", "linh", "ok anh");
m("m038", G, "10", "13:20", "nam", "Demo dùng bản staging hay bản prod anh?");
m("m039", G, "10", "14:00", "hung", "Ai đi họp với khách hàng chiều thứ 4 thay anh được không?", { reactions: [["mai", "like", "14:05"]] });
m("m040", G, "10", "14:30", "tuan", "Tuần này deploy staging vào tối thứ 4 nhé mọi người");

// ---------- Thứ 4, 11/11 — tràng tin ----------
m("m041", G, "11", "09:00:00", "nam", "Anh ơi");
m("m042", G, "11", "09:00:03", "nam", "em mới check lại");
m("m043", G, "11", "09:00:06", "nam", "server staging đang chết");
m("m044", G, "11", "09:00:08", "nam", "@Tuấn restart giúp em staging nhé", { mentions: ["tuan"] });
m("m045", G, "11", "09:00:12", "nam", "gấp ạ");
m("m046", G, "11", "10:00", "linh", "Mình cần viết lại phần FAQ cho trang thanh toán");
m("m047", G, "11", "10:30", "hung", "e Linh review giup a cai PR #490 truoc EOD nhe");
m("m048", G, "11", "10:31", "linh", "oke a");
m("m049", G, "11", "11:00", "tuan", "Staging lên lại rồi nhé");

msgs.sort((a, b) => a.created_at.localeCompare(b.created_at));

const gold = [
  { gold_id: "g01", conversation_key: G, anchor_message_id: "m003", supporting_message_ids: ["m003", "m004", "m030", "m032"], title: "Gửi báo cáo tuần cho anh Hưng", kind: "assigned", assignee_ids: ["u_linh"], due_type: "calendar", due_date: "2026-11-14", final_status: "active", scenario_tags: ["single_message", "later_update_approved", "change_supersede"] },
  { gold_id: "g02", conversation_key: G, anchor_message_id: "m007", supporting_message_ids: ["m007", "m009", "m010"], title: "Cập nhật tài liệu API module voucher", kind: "assigned", assignee_ids: ["u_mai"], due_type: "calendar", due_date: "2026-11-13", final_status: "active", scenario_tags: ["late_anchor", "name_collision", "multi_message"] },
  { gold_id: "g03", conversation_key: G, anchor_message_id: "m014", supporting_message_ids: ["m014", "m015"], title: "Fix 2 bug mã giảm giá", kind: "assigned", assignee_ids: ["u_nam"], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["multi_task_message"] },
  { gold_id: "g04", conversation_key: G, anchor_message_id: "m014", supporting_message_ids: ["m014", "m035"], title: "Làm lại icon set cho màn Checkout", kind: "assigned", assignee_ids: ["u_tuan"], due_type: "none", due_date: null, final_status: "cancelled", scenario_tags: ["multi_task_message", "cancel_approved"] },
  { gold_id: "g05", conversation_key: G, anchor_message_id: "m018", supporting_message_ids: ["m018", "m022"], title: "Review PR #482", kind: "assigned", assignee_ids: ["u_nam"], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["implicit_ask"] },
  { gold_id: "g06", conversation_key: G, anchor_message_id: "m023", supporting_message_ids: ["m023", "m024", "m025"], title: "Làm slide tổng kết sprint", kind: "assigned", assignee_ids: ["u_linh"], due_type: "event", due_date: null, final_status: "active", scenario_tags: ["no_assignee", "event_deadline", "multi_message"] },
  { gold_id: "g07", conversation_key: G, anchor_message_id: "m026", supporting_message_ids: ["m026"], title: "Gửi anh Hưng bản mockup cuối", kind: "self_commit", assignee_ids: ["u_mai"], due_type: "calendar", due_date: "2026-11-10", final_status: "active", scenario_tags: ["self_commit", "name_collision", "relative_deadline"] },
  { gold_id: "g08", conversation_key: G, anchor_message_id: "m036", supporting_message_ids: ["m036", "m036#r1", "m037"], title: "Chuẩn bị slide demo cho khách hàng", kind: "assigned", assignee_ids: ["u_linh", "u_nam"], due_type: "calendar", due_date: "2026-11-19", final_status: "active", scenario_tags: ["multi_assignee"] },
  { gold_id: "g09", conversation_key: DM, anchor_message_id: "d001", supporting_message_ids: ["d001", "d002"], title: "Làm bảng so sánh giá 3 nhà cung cấp", kind: "assigned", assignee_ids: ["u_linh"], due_type: "calendar", due_date: "2026-11-10", final_status: "active", scenario_tags: ["dm_addressee", "declined_not_recreated"] },
  { gold_id: "g10", conversation_key: G, anchor_message_id: "m039", supporting_message_ids: ["m039", "m039#r1"], title: "Đi họp với khách hàng chiều thứ 4 thay anh Hưng", kind: "assigned", assignee_ids: ["u_mai"], due_type: "calendar", due_date: "2026-11-11", final_status: "active", scenario_tags: ["reaction_accept", "implicit_ask"] },
  { gold_id: "g11", conversation_key: G, anchor_message_id: "m044", supporting_message_ids: ["m044"], title: "Restart staging", kind: "assigned", assignee_ids: ["u_tuan"], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["burst"] },
  { gold_id: "g12", conversation_key: G, anchor_message_id: "m046", supporting_message_ids: ["m046"], title: "Viết lại FAQ cho trang thanh toán", kind: "open_ask", assignee_ids: [], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["unanswered_ask"] },
  { gold_id: "g13", conversation_key: G, anchor_message_id: "m047", supporting_message_ids: ["m047", "m048"], title: "Review PR #490", kind: "assigned", assignee_ids: ["u_linh"], due_type: "calendar", due_date: "2026-11-11", final_status: "active", scenario_tags: ["informal_text", "relative_deadline"] },
];

const hardNegatives = ["m011", "m013", "m033", "m034", "d004", "m029"];

const A = (d, hms, who, mid, action) => ({ at: T(d, hms), user_id: U[who].user_id, message_id: mid, action });
const actions = [
  A("09", "09:20", "linh", "m003", "approve"),
  A("09", "09:30", "mai", "m009", "approve"),
  A("09", "10:35", "hung", "m014", "approve"), // người giao bấm duyệt → unauthorized
  A("09", "10:40", "nam", "m014", "approve"),
  A("09", "10:41", "tuan", "m014", "approve"),
  A("09", "16:30", "linh", "d001", "decline"),
  A("10", "09:10", "mai", "m026", "approve"),
  A("10", "10:20", "linh", "m032", "approve_change"),
  A("10", "11:10", "tuan", "m035", "approve_change"),
  A("10", "13:30", "linh", "m036", "approve"),
  A("10", "13:40", "nam", "m036", "decline"),
];

const jsonl = (rows) => rows.map((r) => JSON.stringify(r)).join("\n") + "\n";
writeFileSync(path.join(dir, "messages.jsonl"), jsonl(msgs));
writeFileSync(path.join(dir, "members.json"), JSON.stringify(members, null, 2));
writeFileSync(path.join(dir, "gold.jsonl"), jsonl(gold));
writeFileSync(path.join(dir, "hard_negatives.txt"), hardNegatives.join("\n") + "\n");
writeFileSync(path.join(dir, "user_actions.jsonl"), jsonl(actions));
console.log(`messages=${msgs.length} gold=${gold.length} actions=${actions.length}`);
