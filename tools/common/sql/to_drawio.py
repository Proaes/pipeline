#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sql2drawio.py — SQL CREATE TABLE → draw.io ER图

用法:
    python sql2drawio.py input.sql [-o output.drawio]
    python sql2drawio.py init.sql
    python sql2drawio.py init.sql -o ER图.drawio --lang zh

支持的 SQL 语法:
    CREATE TABLE `table_name` (
      `id` BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
      `name` VARCHAR(50) UNIQUE,
      `role_id` BIGINT,
      PRIMARY KEY (`id`),
      FOREIGN KEY (`role_id`) REFERENCES `role` (`id`)
    );

输出:
    一个可以直接在 draw.io 中打开的 .drawio 文件，
    包含矩形实体、椭圆属性、菱形关系（Chen 记法 ER 图）。
"""

import re
import sys
import os
import json
from collections import OrderedDict
from typing import Optional

# ─── 颜色调色板 ───
PALETTE = [
    ("#DAE8FC", "#6C8EBF"),  # 蓝
    ("#D5E8D4", "#82B366"),  # 绿
    ("#FFE6CC", "#D79B00"),  # 橙
    ("#F8CECC", "#B85450"),  # 红
    ("#E1D5E7", "#9673A6"),  # 紫
    ("#DAE8FC", "#6C8EBF"),  # 青
    ("#FFF2CC", "#D6B656"),  # 黄
    ("#F5F5F5", "#666666"),  # 灰
]

# ─── 中文标签映射 ───
CN_LABELS = {
    "注释": "注释",
}

CN_ENTITY_NAMES = {
    "role": "角色",
    "user": "用户",
    "flower": "鲜花商品",
    "flower_category": "鲜花分类",
    "order": "订单",
    "order_item": "订单明细",
}

CN_FIELD_NAMES = {
    "id": "ID",
    "name": "名称",
    "username": "用户名",
    "password": "密码",
    "email": "邮箱",
    "phone": "手机号",
    "address": "地址",
    "status": "状态",
    "role_id": "角色ID",
    "user_id": "用户ID",
    "category_id": "分类ID",
    "flower_id": "鲜花ID",
    "order_id": "订单ID",
    "order_no": "订单编号",
    "description": "描述",
    "price": "价格",
    "original_price": "原价",
    "total_price": "总价",
    "pay_price": "实付金额",
    "stock": "库存",
    "sales_count": "销量",
    "quantity": "数量",
    "unit_price": "单价",
    "subtotal": "小计",
    "image_url": "图片",
    "flower_language": "花语",
    "color": "颜色",
    "material": "材质",
    "occasion": "适用场合",
    "receiver_name": "收货人",
    "receiver_phone": "收货电话",
    "receiver_address": "收货地址",
    "remark": "备注",
    "create_time": "创建时间",
    "update_time": "更新时间",
    "flower_name": "鲜花名",
}

CN_RELATIONSHIPS = {
    "role_user": "拥有",
    "flower_category_flower": "分类",
    "user_order": "下单",
    "flower_order_item": "包含",
    "order_order_item": "包含",
}


class Column:
    """解析后的列信息。"""
    def __init__(self, name: str, col_type: str, is_pk: bool = False,
                 is_uk: bool = False, is_fk: bool = False,
                 fk_ref: Optional[str] = None, comment: str = ""):
        self.name = name
        self.col_type = col_type
        self.is_pk = is_pk
        self.is_uk = is_uk
        self.is_fk = is_fk
        self.fk_ref = fk_ref  # "other_table"
        self.comment = comment


class Table:
    """解析后的表信息。"""
    def __init__(self, name: str):
        self.name = name.strip("`")
        self.columns: list[Column] = []
        self.comment = ""


def parse_sql(sql_text: str) -> list[Table]:
    """解析 CREATE TABLE 语句，返回 Table 列表。"""
    # 提取 CREATE TABLE 块
    pattern = re.compile(
        r"CREATE\s+TABLE\s+`?(\w+)`?\s*\((.*?)\)"
        r"\s*ENGINE[^;]*;",
        re.DOTALL | re.IGNORECASE
    )
    tables = []

    for match in pattern.finditer(sql_text):
        table_name = match.group(1)
        body = match.group(2)
        table = Table(table_name)

        # 提取 comment
        cm = re.search(r"COMMENT\s*=\s*'([^']*)'", sql_text[match.end()-100:match.end()], re.IGNORECASE)
        if cm:
            table.comment = cm.group(1)

        # 收集 PK, UNIQUE, FK 约束
        pk_cols = set()
        uk_cols = set()
        fk_map: dict[str, str] = {}  # col -> ref_table

        # PRIMARY KEY (`id`)
        for pk in re.finditer(r"PRIMARY\s+KEY\s*\(`(\w+)`\)", body, re.IGNORECASE):
            pk_cols.add(pk.group(1))

        # UNIQUE KEY `uk_name` (`col`)
        for uk in re.finditer(r"UNIQUE\s+KEY\s+\S+\s*\(`(\w+)`\)", body, re.IGNORECASE):
            uk_cols.add(uk.group(1))

        # UNIQUE 内联
        for uc in re.finditer(r"^`(\w+)`[^,]+UNIQUE", body, re.IGNORECASE | re.MULTILINE):
            uk_cols.add(uc.group(1))

        # FOREIGN KEY (`col`) REFERENCES `tbl` (`col2`)
        for fk in re.finditer(
                r"FOREIGN\s+KEY\s*\(`(\w+)`\)\s*REFERENCES\s*`(\w+)`",
                body, re.IGNORECASE):
            fk_map[fk.group(1)] = fk.group(2)

        # 逐列解析
        lines = body.split("\n")
        for line in lines:
            line = line.strip().rstrip(",")
            # 跳过约束行
            if re.match(r"^\s*(PRIMARY\s+KEY|UNIQUE\s+KEY|FOREIGN\s+KEY|KEY\s+|INDEX\s+|CONSTRAINT\s+)", line, re.IGNORECASE):
                continue

            col_match = re.match(r"`(\w+)`\s+(\w[\w\s]*(?:\(\d+(?:,\d+)?\))?)\s*(.*)", line)
            if not col_match:
                continue

            col_name = col_match.group(1)
            col_type = col_match.group(2).strip()
            remainder = col_match.group(3)

            is_pk = col_name in pk_cols or "AUTO_INCREMENT" in remainder.upper()
            is_uk = col_name in uk_cols or "UNIQUE" in remainder.upper()
            is_fk = col_name in fk_map

            # 提取 COMMENT
            comment = ""
            cm = re.search(r"COMMENT\s+'([^']*)'", remainder, re.IGNORECASE)
            if cm:
                comment = cm.group(1)

            table.columns.append(Column(
                name=col_name,
                col_type=col_type,
                is_pk=is_pk,
                is_uk=is_uk,
                is_fk=is_fk,
                fk_ref=fk_map.get(col_name),
                comment=comment,
            ))

        tables.append(table)

    return tables


def column_label(col: Column, lang: str) -> str:
    """返回列的显示标签。"""
    if lang == "zh" and col.name in CN_FIELD_NAMES:
        label = CN_FIELD_NAMES[col.name]
    elif lang == "zh" and col.comment:
        label = col.comment
    else:
        label = col.name
    return label


def table_label(table: Table, lang: str) -> str:
    """返回表的显示标签。"""
    if lang == "zh" and table.name in CN_ENTITY_NAMES:
        return CN_ENTITY_NAMES[table.name]
    if lang == "zh" and table.comment:
        return table.comment
    return table.name


def relation_label(table_a: str, table_b: str, lang: str) -> str:
    """返回关系的显示标签。"""
    key = f"{table_a}_{table_b}"
    if lang == "zh" and key in CN_RELATIONSHIPS:
        return CN_RELATIONSHIPS[key]
    # 回退
    key2 = f"{table_b}_{table_a}"
    if lang == "zh" and key2 in CN_RELATIONSHIPS:
        return CN_RELATIONSHIPS[key2]
    return "关联"


# ─── draw.io XML 生成 ───

LAYOUT = {
    "entity_width": 130,
    "entity_height": 40,
    "attr_width": 60,
    "attr_height": 30,
    "rel_size": (55, 35),
    "x_spacing": 200,
    "y_spacing": 160,
    "start_x": 40,
    "start_y": 100,
    "attr_gap_x": 55,
    "attr_gap_y": -50,
}


def _make_geom(x: int, y: int, w: int, h: int) -> str:
    return f'      <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>\n'


def _make_entity(cid: str, label: str, fill: str, stroke: str, x: int, y: int) -> str:
    w = LAYOUT["entity_width"]
    h = LAYOUT["entity_height"]
    return (
        f'    <mxCell id="{cid}" value="{_xml_escape(label)}" '
        f'style="rounded=0;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};fontStyle=1;fontSize=13;" vertex="1" parent="1">\n'
        f'{_make_geom(x, y, w, h)}'
        f'    </mxCell>\n'
    )


def _make_attr(cid: str, label: str, stroke: str, is_pk: bool, x: int, y: int) -> str:
    w = LAYOUT["attr_width"]
    h = LAYOUT["attr_height"]
    fs = "fontStyle=1;" if is_pk else ""
    return (
        f'    <mxCell id="{cid}" value="{_xml_escape(label)}" '
        f'style="ellipse;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor={stroke};{fs}fontSize=10;" vertex="1" parent="1">\n'
        f'{_make_geom(x, y, w, h)}'
        f'    </mxCell>\n'
    )


def _make_edge(cid: str, source: str, target: str, stroke_width: float = 1.0) -> str:
    return (
        f'    <mxCell id="{cid}" style="endArrow=none;html=1;rounded=0;strokeWidth={stroke_width};" '
        f'edge="1" parent="1" source="{source}" target="{target}">\n'
        f'      <mxGeometry relative="1" as="geometry"/>\n'
        f'    </mxCell>\n'
    )


def _make_relationship(cid: str, label: str, x: int, y: int) -> str:
    w, h = LAYOUT["rel_size"]
    return (
        f'    <mxCell id="{cid}" value="{_xml_escape(label)}" '
        f'style="rhombus;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000000;fontSize=10;" vertex="1" parent="1">\n'
        f'{_make_geom(x, y, w, h)}'
        f'    </mxCell>\n'
    )


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def generate_drawio(tables: list[Table], lang: str = "en") -> str:
    """从解析后的表列表生成 .drawio XML 字符串。"""
    counter = [0]
    def next_id(prefix: str = "c") -> str:
        counter[0] += 1
        return f"{prefix}-{counter[0]}"

    lines = []
    lines.append(
        '<mxfile host="app.diagrams.net" modified="2026-01-01T00:00:00.000Z" agent="sql2drawio" version="24.0.0" type="device">\n'
    )
    lines.append('  <diagram name="ER图" id="er">\n')
    lines.append(
        '    <mxGraphModel dx="1400" dy="1000" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="1400" pageHeight="1000">\n'
    )
    lines.append('      <root>\n')
    lines.append('        <mxCell id="0"/>\n')
    lines.append('        <mxCell id="1" parent="0"/>\n')

    # ── 布局 ──
    # 简单的网格布局：每行 3 个表
    cols = 3
    start_x = LAYOUT["start_x"]
    start_y = LAYOUT["start_y"]
    col_spacing = LAYOUT["x_spacing"]
    row_spacing = LAYOUT["y_spacing"]

    entity_ids: dict[str, str] = {}    # table_name -> mxCell id
    entity_pos: dict[str, tuple[int,int]] = {}
    attr_ids: dict[str, list[str]] = {}  # table_name -> [attr_id, ...]
    relations_added: set[tuple[str,str]] = set()

    # 生成实体 + 属性
    for idx, table in enumerate(tables):
        color_i = idx % len(PALETTE)
        fill, stroke = PALETTE[color_i]
        row, col = divmod(idx, cols)
        entity_x = start_x + col * (LAYOUT["entity_width"] + col_spacing)
        entity_y = start_y + row * row_spacing

        ent_id = next_id("ent")
        entity_ids[table.name] = ent_id
        entity_pos[table.name] = (entity_x, entity_y)

        label = table_label(table, lang)
        lines.append(_make_entity(ent_id, label, fill, stroke, entity_x, entity_y))

        # 属性：围绕实体排列
        attr_ids[table.name] = []
        n = len(table.columns)
        if n == 0:
            continue

        # 属性放在实体的上方，均匀分布
        total_width = n * LAYOUT["attr_width"] + (n - 1) * 10
        attr_start_x = entity_x + (LAYOUT["entity_width"] - total_width) // 2
        attr_y = entity_y + LAYOUT["attr_gap_y"]

        for ci, col in enumerate(table.columns):
            aid = next_id("a")
            attr_ids[table.name].append(aid)
            ax = attr_start_x + ci * (LAYOUT["attr_width"] + 10)
            c_label = column_label(col, lang)
            # FK 属性用斜体
            attr_stroke = stroke
            lines.append(_make_attr(aid, c_label, attr_stroke, col.is_pk, ax, attr_y))

            # 属性连线
            eid = next_id("e")
            lines.append(_make_edge(eid, aid, ent_id))

    # 生成关系（基于 FK）
    for table in tables:
        for col in table.columns:
            if col.is_fk and col.fk_ref and col.fk_ref in entity_ids:
                pair = (table.name, col.fk_ref)
                # 避免重复关系
                reverse = (col.fk_ref, table.name)
                if pair in relations_added or reverse in relations_added:
                    continue
                relations_added.add(pair)

                # 关系菱形放在两个实体中间
                if table.name in entity_pos and col.fk_ref in entity_pos:
                    x1, y1 = entity_pos[table.name]
                    x2, y2 = entity_pos[col.fk_ref]
                    rx = (x1 + x2 + LAYOUT["entity_width"]) // 2 - LAYOUT["rel_size"][0] // 2
                    ry = (y1 + y2 + LAYOUT["entity_height"]) // 2 - LAYOUT["rel_size"][1] // 2

                    rel_id = next_id("rel")
                    rlabel = relation_label(table.name, col.fk_ref, lang)
                    lines.append(_make_relationship(rel_id, rlabel, rx, ry))

                    # 连线：实体A → 菱形 → 实体B
                    e_a = next_id("er")
                    e_b = next_id("er")
                    lines.append(_make_edge(e_a, entity_ids[col.fk_ref], rel_id, 1.5))
                    lines.append(_make_edge(e_b, rel_id, entity_ids[table.name], 1.5))

    # ── 图例 ──
    legend_y = start_y + ((len(tables) + cols - 1) // cols) * row_spacing + 40
    lines.append(
        f'    <mxCell id="leg-bg" value="" style="rounded=0;whiteSpace=wrap;html=1;fillColor=#F5F5F5;strokeColor=#999999;" vertex="1" parent="1">\n'
        f'      <mxGeometry x="{start_x}" y="{legend_y}" width="600" height="36" as="geometry"/>\n'
        f'    </mxCell>\n'
    )
    leg_labels = (
        ("实体", "rounded=0;whiteSpace=wrap;html=1;fillColor=#DAE8FC;strokeColor=#6C8EBF;fontSize=9;"),
        ("属性", "ellipse;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000;fontSize=9;"),
        ("关系", "rhombus;whiteSpace=wrap;html=1;fillColor=#FFFFFF;strokeColor=#000;fontSize=9;"),
    )
    if lang == "zh":
        desc = "粗体 = 主键  |  菱形 = 实体间关系 (N:1)"
    else:
        desc = "Bold = PK  |  Diamond = Relationship (N:1)"
    for i, (lbl, sty) in enumerate(leg_labels):
        lx = start_x + 12 + i * 110
        lines.append(
            f'    <mxCell id="leg-{i}" value="{lbl}" style="{sty}" vertex="1" parent="1">\n'
            f'      <mxGeometry x="{lx}" y="{legend_y + 7}" width="50" height="22" as="geometry"/>\n'
            f'    </mxCell>\n'
        )
    lines.append(
        f'    <mxCell id="leg-t" value="{desc}" style="text;html=1;fontSize=9;fontColor=#666666;" vertex="1" parent="1">\n'
        f'      <mxGeometry x="{start_x + 340}" y="{legend_y + 8}" width="250" height="20" as="geometry"/>\n'
        f'    </mxCell>\n'
    )

    # ── 收尾 ──
    lines.append("      </root>\n")
    lines.append("    </mxGraphModel>\n")
    lines.append("  </diagram>\n")
    lines.append("</mxfile>\n")

    return "".join(lines)


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="SQL CREATE TABLE → draw.io ER 图",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python sql2drawio.py init.sql
  python sql2drawio.py init.sql -o ER图.drawio
  python sql2drawio.py init.sql -o ER图.drawio --lang zh
        """,
    )
    parser.add_argument("input", help="输入 SQL 文件路径（含 CREATE TABLE 语句）")
    parser.add_argument("-o", "--output", default=None, help="输出 .drawio 文件路径（默认: input.drawio）")
    parser.add_argument("--lang", choices=["en", "zh"], default="en",
                        help="显示语言 [默认: en]")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        sys.exit(f"错误: 文件不存在 — {args.input}")

    with open(args.input, "r", encoding="utf-8") as f:
        sql_text = f.read()

    tables = parse_sql(sql_text)
    if not tables:
        sys.exit("错误: 未找到 CREATE TABLE 语句")

    print(f"[OK] 解析到 {len(tables)} 张表:")
    for t in tables:
        print(f"  - {t.name} ({len(t.columns)} 列)")

    xml = generate_drawio(tables, lang=args.lang)

    output_path = args.output or os.path.splitext(args.input)[0] + ".drawio"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(xml)

    print(f"[OK] ER 图已生成 -> {output_path}")
