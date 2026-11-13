// Sinh data/sample/* (dữ liệu TỰ SINH, chỉ để chạy thông code — không phải bộ đánh giá).
// Chạy (trong backend/): node data/gen_sample.mjs
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
    // giờ reaction "HH:MM" (cùng ngày với tin) hoặc "DD|HH:MM" (ngày khác)
    reactions: (extra.reactions ?? []).map(([k, type, at]) => ({ user_id: U[k].user_id, type, created_at: at.includes("|") ? T(...at.split("|")) : T(d, at) })),
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

// ---------- Ca test "Ngữ cảnh mới" (task được link, 3 task gần nhất, tìm task) ----------
m("m050", G, "11", "15:00", "hung", "@Nam viết unit test cho module voucher trước thứ 6 nhé", { mentions: ["nam"] });
m("m051", G, "11", "15:01", "nam", "dc anh", { reply: "m050" });
m("m052", G, "11", "16:00", "hung", "Ai rảnh viết giúp anh release note cho bản 2.3 không?", { reactions: [["tuan", "like", "12|10:00"]] }); // reaction_link (hôm sau)
m("m053", G, "11", "16:30", "hung", "@Mai làm giúp anh banner khuyến mãi Black Friday nhé", { mentions: ["mai"] });

// ---------- Thứ 5, 12/11 — kênh bận (≥ 10 task đang mở) ----------
m("m054", G, "12", "08:45", "linh", "Chào cả nhà");
m("m055", G, "12", "09:00", "tuan", "Bản 2.3 build xong rồi nhé mọi người");
m("m056", G, "12", "09:30", "mai", "Em đang làm nốt phần màn xác nhận đơn");
m("m057", G, "12", "10:30", "nam", "Anh Hưng ơi bên khách hàng hẹn demo lúc 2h chiều thứ 5 tuần sau ạ");
m("m058", G, "12", "11:00", "hung", "@Mai tài liệu API voucher lùi sang thứ 2 nhé, chờ backend chốt endpoint", { mentions: ["mai"] }); // far_update_no_reply
m("m059", G, "12", "11:01", "mai", "dạ vâng anh");
m("m060", G, "12", "13:30", "linh", "Animation chốt Lottie hay CSS chưa anh ơi");
m("m061", G, "12", "13:35", "hung", "CSS nhé");
m("m062", G, "12", "14:00", "hung", "lùi sang thứ 2 nhé, thứ 6 team còn bận chuẩn bị demo", { reply: "m050" }); // reply_link_old
m("m063", G, "12", "14:01", "nam", "dạ ok anh", { reply: "m062" });
m("m064", G, "12", "15:00", "hung", "@Nam review PR #482 xong trước thứ 6 nhé", { mentions: ["nam"] }); // busy_channel_10
m("m065", G, "12", "15:30", "tuan", "Mai ơi banner Black Friday làm luôn đi em, sếp hỏi rồi"); // recreate_declined_far
m("m066", G, "12", "16:00", "linh", "Chiều nay em về sớm 30 phút nhé mọi người");

// ---------- Thứ 6, 13/11 ----------
m("m067", G, "13", "08:30", "hung", "@Linh weekly report dời sang thứ 2 tuần sau nhé", { mentions: ["linh"] }); // paraphrase
m("m068", G, "13", "08:31", "linh", "dạ");
m("m069", G, "13", "10:00", "hung", "cái đó xong chưa em?"); // ambiguous_no_reply
m("m070", G, "13", "10:05", "nam", "anh hỏi em hay chị Linh ạ?");
// ---- nhắc lại việc cũ bằng cách diễn đạt khác (paraphrase) — không reply, không chung từ khoá chính ----
m("m072", G, "13", "13:00", "hung", "@Nam cái test tự động cho phần coupon lùi sang thứ 3 tuần sau nhé", { mentions: ["nam"] }); // ↔ g14 unit test module voucher
m("m073", G, "13", "14:00", "hung", "@Linh bộ trình bày cho buổi gặp đối tác dời sang thứ 4 tuần sau nhé", { mentions: ["linh"] }); // ↔ g08 slide demo khách hàng
m("m074", G, "13", "15:00", "hung", "Mai ơi spec endpoint phần coupon không cần viết nữa nhé, backend tự lo", {}); // ↔ g02 tài liệu API voucher (huỷ)
m("m076", G, "13", "16:30", "nam", "Hôm nay bên đối tác khen bộ trình bày lắm mọi người"); // hard negative gần nghĩa

// ---------- Ca test v5 (code tìm trước, luật cứng) — thứ 2 16/11, thứ 3 17/11 ----------
m("m080", G, "16", "09:00", "hung", "@Tuấn release note bản 2.3 xong trước thứ 4 nhé", { mentions: ["tuan"] }); // matched_far_no_reply ↔ g15
m("m081", G, "16", "09:10", "hung", "@Nam viết script backup database hằng ngày nhé", { mentions: ["nam"] }); // g17 — Nam từ chối 09:30
m("m082", G, "16", "10:00", "mai", "Sáng nay em họp với bên brand, họ ưng bảng màu mới rồi mọi người");
m("m083", G, "17", "09:00", "hung", "Nam ơi cái backup database tính sao rồi em, làm giúp anh nhé"); // recreate_declined_soft (2 từ khoá chung)
m("m084", G, "17", "09:30", "hung", "@Tuấn làm lại icon set cho màn Checkout đi em, khách đổi ý rồi", { mentions: ["tuan"] }); // cancelled_then_reassigned ↔ g04 đã huỷ
m("m085", G, "17", "09:31", "tuan", "dạ ok anh", { reply: "m084" });
m("m086", G, "17", "10:00", "hung", "@Linh cái anh nhờ xem hôm thứ 4 em làm chưa, chưa thì để thứ 5 cũng được", { mentions: ["linh"] }); // llm_flag_fallback ↔ g13 (không chung từ khoá)
m("m087", G, "17", "11:00", "hung", "à mà @Nam PR #482 em review xong chưa, anh cần trước thứ 4", { mentions: ["nam"], reply: "m047" }); // reply_but_other_task: reply vào việc của Linh (g13), nói g05
m("m088", G, "17", "16:00", "tuan", "release note bản 2.3 em đăng lên wiki rồi nhé"); // hard negative: báo xong

msgs.sort((a, b) => a.created_at.localeCompare(b.created_at));

const gold = [
  { gold_id: "g01", conversation_key: G, anchor_message_id: "m003", supporting_message_ids: ["m003", "m004", "m030", "m032", "m067"], title: "Gửi báo cáo tuần cho anh Hưng", kind: "assigned", assignee_ids: ["u_linh"], due_type: "calendar", due_date: "2026-11-16", final_status: "active", scenario_tags: ["single_message", "later_update_approved", "change_supersede", "far_update_no_reply", "paraphrase", "matched_paraphrase"] },
  { gold_id: "g02", conversation_key: G, anchor_message_id: "m007", supporting_message_ids: ["m007", "m009", "m010", "m058", "m074"], title: "Cập nhật tài liệu API module voucher", kind: "assigned", assignee_ids: ["u_mai"], due_type: "calendar", due_date: "2026-11-16", final_status: "cancelled", scenario_tags: ["late_anchor", "name_collision", "multi_message", "far_update_no_reply", "paraphrase", "cancel_old_no_reply"] },
  { gold_id: "g03", conversation_key: G, anchor_message_id: "m014", supporting_message_ids: ["m014", "m015"], title: "Fix 2 bug mã giảm giá", kind: "assigned", assignee_ids: ["u_nam"], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["multi_task_message"] },
  { gold_id: "g04", conversation_key: G, anchor_message_id: "m014", supporting_message_ids: ["m014", "m035"], title: "Làm lại icon set cho màn Checkout", kind: "assigned", assignee_ids: ["u_tuan"], due_type: "none", due_date: null, final_status: "cancelled", scenario_tags: ["multi_task_message", "cancel_approved", "cancel_old_no_reply"] },
  { gold_id: "g05", conversation_key: G, anchor_message_id: "m018", supporting_message_ids: ["m018", "m022", "m064", "m087"], title: "Review PR #482", kind: "assigned", assignee_ids: ["u_nam"], due_type: "calendar", due_date: "2026-11-18", final_status: "active", scenario_tags: ["implicit_ask", "busy_channel_10", "reply_but_other_task"] },
  { gold_id: "g06", conversation_key: G, anchor_message_id: "m023", supporting_message_ids: ["m023", "m024", "m025"], title: "Làm slide tổng kết sprint", kind: "assigned", assignee_ids: ["u_linh"], due_type: "event", due_date: null, final_status: "active", scenario_tags: ["no_assignee", "event_deadline", "multi_message"] },
  { gold_id: "g07", conversation_key: G, anchor_message_id: "m026", supporting_message_ids: ["m026"], title: "Gửi anh Hưng bản mockup cuối", kind: "self_commit", assignee_ids: ["u_mai"], due_type: "calendar", due_date: "2026-11-10", final_status: "active", scenario_tags: ["self_commit", "name_collision", "relative_deadline"] },
  { gold_id: "g08", conversation_key: G, anchor_message_id: "m036", supporting_message_ids: ["m036", "m036#r1", "m037", "m073"], title: "Chuẩn bị slide demo cho khách hàng", kind: "assigned", assignee_ids: ["u_linh", "u_nam"], due_type: "calendar", due_date: "2026-11-18", final_status: "active", scenario_tags: ["multi_assignee", "paraphrase"] },
  { gold_id: "g09", conversation_key: DM, anchor_message_id: "d001", supporting_message_ids: ["d001", "d002"], title: "Làm bảng so sánh giá 3 nhà cung cấp", kind: "assigned", assignee_ids: ["u_linh"], due_type: "calendar", due_date: "2026-11-10", final_status: "active", scenario_tags: ["dm_addressee", "declined_not_recreated"] },
  { gold_id: "g10", conversation_key: G, anchor_message_id: "m039", supporting_message_ids: ["m039", "m039#r1"], title: "Đi họp với khách hàng chiều thứ 4 thay anh Hưng", kind: "assigned", assignee_ids: ["u_mai"], due_type: "calendar", due_date: "2026-11-11", final_status: "active", scenario_tags: ["reaction_accept", "implicit_ask"] },
  { gold_id: "g11", conversation_key: G, anchor_message_id: "m044", supporting_message_ids: ["m044"], title: "Restart staging", kind: "assigned", assignee_ids: ["u_tuan"], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["burst"] },
  { gold_id: "g12", conversation_key: G, anchor_message_id: "m046", supporting_message_ids: ["m046"], title: "Viết lại FAQ cho trang thanh toán", kind: "open_ask", assignee_ids: [], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["unanswered_ask"] },
  { gold_id: "g13", conversation_key: G, anchor_message_id: "m047", supporting_message_ids: ["m047", "m048", "m086"], title: "Review PR #490", kind: "assigned", assignee_ids: ["u_linh"], due_type: "calendar", due_date: "2026-11-19", final_status: "active", scenario_tags: ["informal_text", "relative_deadline", "llm_flag_fallback"] },
  // ---- ca test "Ngữ cảnh mới" ----
  { gold_id: "g14", conversation_key: G, anchor_message_id: "m050", supporting_message_ids: ["m050", "m051", "m062", "m072"], title: "Viết unit test cho module voucher", kind: "assigned", assignee_ids: ["u_nam"], due_type: "calendar", due_date: "2026-11-17", final_status: "active", scenario_tags: ["reply_link_old", "paraphrase"] },
  { gold_id: "g15", conversation_key: G, anchor_message_id: "m052", supporting_message_ids: ["m052", "m052#r1", "m080"], title: "Viết release note cho bản 2.3", kind: "assigned", assignee_ids: ["u_tuan"], due_type: "calendar", due_date: "2026-11-18", final_status: "active", scenario_tags: ["reaction_link", "implicit_ask", "matched_far_no_reply"] },
  // m065 nhắc lại việc đã bị Mai từ chối: nằm trong supporting để một task tạo lại từ m065 được ghép vào nhãn → đếm "tạo lại"
  { gold_id: "g16", conversation_key: G, anchor_message_id: "m053", supporting_message_ids: ["m053", "m065"], title: "Làm banner khuyến mãi Black Friday", kind: "assigned", assignee_ids: ["u_mai"], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["recreate_declined_far", "declined_not_recreated", "recreate_declined_hard"] },
  // ---- ca test v5 ----
  // m083 nhắc lại việc Nam đã từ chối, chỉ 2 từ khoá chung (backup, database) → không đủ luật cứng, đi đường hỏi lại LLM
  { gold_id: "g17", conversation_key: G, anchor_message_id: "m081", supporting_message_ids: ["m081", "m083"], title: "Viết script backup database hằng ngày", kind: "assigned", assignee_ids: ["u_nam"], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["recreate_declined_soft", "declined_not_recreated"] },
  // icon set đã huỷ (g04) được giao lại → task MỚI hợp lệ (luật cứng không áp cho cancelled)
  { gold_id: "g18", conversation_key: G, anchor_message_id: "m084", supporting_message_ids: ["m084", "m085"], title: "Làm lại icon set cho màn Checkout", kind: "assigned", assignee_ids: ["u_tuan"], due_type: "none", due_date: null, final_status: "active", scenario_tags: ["cancelled_then_reassigned"] },
];

const hardNegatives = ["m011", "m013", "m033", "m034", "d004", "m029", "m055", "m057", "m060", "m069" /* ambiguous_no_reply */, "m076" /* gần nghĩa nhưng không phải việc */, "m082", "m088" /* báo xong việc g15 */];

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
  A("11", "15:10", "nam", "m050", "approve"),
  A("11", "16:40", "mai", "m053", "decline"),
  A("16", "09:30", "nam", "m081", "decline"),
];

const jsonl = (rows) => rows.map((r) => JSON.stringify(r)).join("\n") + "\n";
writeFileSync(path.join(dir, "messages.jsonl"), jsonl(msgs));
writeFileSync(path.join(dir, "members.json"), JSON.stringify(members, null, 2));
writeFileSync(path.join(dir, "gold.jsonl"), jsonl(gold));
writeFileSync(path.join(dir, "hard_negatives.txt"), hardNegatives.join("\n") + "\n");
writeFileSync(path.join(dir, "user_actions.jsonl"), jsonl(actions));
console.log(`messages=${msgs.length} gold=${gold.length} actions=${actions.length}`);
