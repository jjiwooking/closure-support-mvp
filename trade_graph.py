"""
중고 설비 처분을 위한 LangGraph 서브그래프 (심사평 "멀티에이전트 역할 분배 고도화",
"당근마켓 등 외부 채널 연계" 대응). app.py의 screen_equipment()에서 호출한다.

노드 흐름:
  price_estimate : pricing_model(ML, KNN 회귀)로 유사 사례 기반 적정가를 추정한다.
  listing_draft   : LLM으로 판매 글을 작성한다. 입력된 사실과 추정가 범위만
                     근거로 쓰고 지어내지 않도록 가이드봇과 동일한 시스템 지시를
                     쓴다. LLM 미설정/실패 시 기존 services.generate_listing
                     템플릿으로 폴백한다(신뢰성 원칙 유지).
  channel_guide   : 카테고리를 당근마켓 카테고리명으로 매핑하고, 자체 거래 대신
                     당근마켓 등 외부 채널에 올릴 때 참고할 안내문을 만든다.
                     공식 API가 없으므로 자동 게시는 하지 않는다.
"""
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

import pricing_model
import services
from config import llm_configured
from llm_client import generate_text

# 자체 카테고리 -> 당근마켓에 올릴 때 참고할 카테고리명(고정 매핑, 외부 API 없음).
DAANGN_CATEGORY_MAP = {
    "주방/조리기기": "생활가전 > 주방가전",
    "냉장/냉동": "생활가전 > 냉장/냉동고",
    "카페/음료기기": "생활가전 > 커피/차용품",
    "집기/가구": "가구/인테리어",
    "전자기기": "디지털기기",
    "기타": "기타 중고물품",
}


class TradeState(TypedDict):
    conn: Any
    item: dict
    style: str
    predicted_price: dict
    draft: str
    draft_source: str
    channel_tip: str


def price_estimate(state: TradeState) -> dict:
    conn = state["conn"]
    item = state["item"]
    predicted = pricing_model.predict_price(
        conn, item.get("category"), item.get("used_period"), item.get("condition")
    )
    return {"predicted_price": predicted}


def _price_hint_text(predicted: dict) -> str:
    if not predicted.get("ok"):
        return ""
    low, high = predicted["price_range"]
    return (
        f"AI 추정 시세는 {predicted['predicted_price']:,}원"
        f"(유사 사례 {low:,}~{high:,}원) 정도입니다. "
        "이 추정가는 참고용이며 실제 희망 가격은 판매자가 정한 값을 그대로 씁니다."
    )


def _llm_listing(item: dict, style: str, predicted: dict):
    """실패하면 None을 반환해 호출부가 services.generate_listing 템플릿으로
    폴백하게 한다. 입력된 사실 외에 성능/무하자/가격 근거를 지어내지 않도록 한다."""
    style_desc = "2~3문장의 짧은 글" if style == "short" else "친근한 어투의 블로그 스타일 글"
    system_instruction = (
        "당신은 폐업을 준비하는 소상공인의 중고 집기 판매 글을 써주는 도우미입니다. "
        "아래 '물품 정보'에 없는 성능, 무하자 여부, 가격 근거를 지어내지 마세요. "
        "미입력 항목은 자연스럽게 '확인 필요'로 안내하세요. "
        f"{style_desc}로 작성하세요."
    )
    fields = [
        f"물품명: {item.get('name') or '확인 필요'}",
        f"모델: {item.get('model') or '확인 필요'}",
        f"사용 기간: {item.get('used_period') or '확인 필요'}",
        f"상태: {item.get('condition') or '확인 필요'}",
        f"하자: {item.get('defects') or '확인 필요'}",
        f"희망 가격: {item.get('asking_price') or '확인 필요'}",
        f"수거 조건: {item.get('pickup_terms') or '확인 필요'}",
    ]
    price_hint = _price_hint_text(predicted)
    prompt = "물품 정보:\n" + "\n".join(fields)
    if price_hint:
        prompt += f"\n\n참고(AI 추정 시세, 근거로만 활용하고 본문에 그대로 못박지 말 것): {price_hint}"
    prompt += "\n\n위 정보만 바탕으로 판매 글을 작성해주세요."

    result = generate_text(prompt, system_instruction=system_instruction)
    if result["ok"] and result["text"]:
        return result["text"].strip()
    return None


def listing_draft(state: TradeState) -> dict:
    item = state["item"]
    style = state["style"]
    predicted = state["predicted_price"]

    if llm_configured():
        text = _llm_listing(item, style, predicted)
        if text:
            return {"draft": text, "draft_source": "llm"}

    template = services.generate_listing(item, style=style)
    return {"draft": template, "draft_source": "template"}


def channel_guide(state: TradeState) -> dict:
    category = state["item"].get("category")
    daangn_category = DAANGN_CATEGORY_MAP.get(category, "기타 중고물품")
    tip = (
        f"당근마켓에 올릴 때 추천 카테고리: **{daangn_category}**\n"
        "위 판매 글을 복사해 당근마켓 앱의 '내 물건 팔기'에 붙여넣고, "
        "사진을 추가해서 올리면 더 빨리 거래될 수 있어요. "
        "(자동 게시 기능은 없으며, 게시는 직접 진행해야 합니다.)"
    )
    return {"channel_tip": tip}


def _build_graph():
    workflow = StateGraph(TradeState)
    workflow.add_node("price_estimate", price_estimate)
    workflow.add_node("listing_draft", listing_draft)
    workflow.add_node("channel_guide", channel_guide)
    workflow.add_edge(START, "price_estimate")
    workflow.add_edge("price_estimate", "listing_draft")
    workflow.add_edge("listing_draft", "channel_guide")
    workflow.add_edge("channel_guide", END)
    return workflow.compile()


_GRAPH = _build_graph()


def run_trade(conn, item: dict, style: str = "short") -> dict:
    initial: TradeState = {
        "conn": conn,
        "item": item,
        "style": style,
        "predicted_price": {},
        "draft": "",
        "draft_source": "template",
        "channel_tip": "",
    }
    result = _GRAPH.invoke(initial)
    return {
        "draft": result["draft"],
        "draft_source": result["draft_source"],
        "predicted_price": result["predicted_price"],
        "channel_tip": result["channel_tip"],
    }
