import os
import json
from tools.chroma_tool import chroma_db, search_menu_items_with_ids

def test_search():
    queries = ["我想吃东坡肉", "红烧肉有吗", "推荐几个辣的菜", "有没有素食"]
    
    for query in queries:
        print(f"\n查询: {query}")
        result = search_menu_items_with_ids(query, top_k=3)
        for i, (content, score) in enumerate(zip(result['contents'], result['scores'])):
            print(f"  {i+1}. [得分: {score:.4f}] {content}")

if __name__ == "__main__":
    test_search()
