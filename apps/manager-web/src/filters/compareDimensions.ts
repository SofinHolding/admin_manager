/**
 * Các chiều so sánh được phép xuất hiện trong "Biểu đồ chung".
 *
 * Yêu cầu (architecture-assessment-v2 §2.6): so sánh QUỐC GIA nằm trong biểu đồ RIÊNG
 * (`views-by-country`); "Biểu đồ chung" KHÔNG render lại. Kiểu `CommonCompareDimension` loại `country`
 * ngay ở compile-time, và test kiểm tra danh sách ở runtime.
 */

import type { CommonCompareDimension } from "../api";

export type { CommonCompareDimension };

export const COMMON_COMPARE_DIMENSIONS = ["platform", "owner", "channel", "group"] as const satisfies readonly CommonCompareDimension[];
