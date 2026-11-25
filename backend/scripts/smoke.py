"""Exercise the real AI pipeline through a local API and its worker."""

import argparse
from time import monotonic, sleep
from uuid import uuid4

import httpx


def request(client, method, path, *, idempotent=False, **kwargs):
    headers = {"Idempotency-Key": uuid4().hex} if idempotent else None
    response = client.request(method, path, headers=headers, **kwargs)
    if not response.is_success:
        error = response.json()
        raise RuntimeError(
            f"{method} {path}: {response.status_code} {error.get('code', 'UNKNOWN')}"
        )
    return response.json()


def wait_for(client, path, timeout, field=None):
    started = monotonic()
    until = monotonic() + timeout
    previous = None
    while monotonic() < until:
        result = request(client, "GET", path)
        job = result[field] if field else result
        state = (job["status"], job.get("phase"))
        if state != previous:
            print(f"已等 {monotonic() - started:.0f} 秒，当前状态：{state}", flush=True)
            previous = state
        if job["status"] == "SUCCEEDED":
            return result
        if job["status"] == "FAILED":
            error = job.get("error") or {}
            raise RuntimeError(f"AI task failed: {error.get('code')} ({error.get('requestId')})")
        sleep(2)
    raise TimeoutError(f"AI task did not finish before local timeout: {path}")


def run_topic(client, topic, timeout, *, expect_saved=False):
    job = request(client, "POST", "/v1/generation-jobs", json={"topic": topic}, idempotent=True)
    print(f"{topic} 生成任务已提交 {job['jobId']}", flush=True)
    generated = wait_for(client, f"/v1/generation-jobs/{job['jobId']}", timeout)
    if expect_saved:
        saved = request(client, "GET", "/v1/topics")["items"]
        assert (
            sum(
                item["title"] == generated["topicTitle"] and item["art"] == "topic"
                for item in saved
            )
            == 1
        )
    attempt = request(
        client, "POST", "/v1/attempts", json={"quizId": generated["quizId"]}, idempotent=True
    )
    path = f"/v1/attempts/{attempt['attemptId']}"
    receipts = []
    types = set()
    total_questions = attempt["totalQuestions"]
    assert total_questions == 12
    for index in range(total_questions):
        question = attempt["question"]
        assert question["index"] == index and question["aiGenerated"]
        assert "correctOptionIds" not in question and "explanation" not in question
        types.add(question["type"])
        selected = [question["options"][0]["optionId"]]
        receipt_path = f"{path}/answers/{question['questionId']}"
        receipt = request(client, "PUT", receipt_path, json={"selectedOptionIds": selected})
        assert receipt["answeredCount"] == index + 1 and receipt["explanation"]
        assert receipt["evidence"] and all(
            evidence["sourceUrl"].startswith("https://") for evidence in receipt["evidence"]
        )
        if index == 0:
            changed = [question["options"][1]["optionId"]]
            assert (
                request(client, "PUT", receipt_path, json={"selectedOptionIds": changed}) == receipt
            )
        receipts.append(receipt)
        if index < total_questions - 1:
            assert receipt["recordId"] is None
            attempt = request(client, "GET", path)

    assert types == {"single", "multi", "judge"}
    sources = {evidence["sourceUrl"] for receipt in receipts for evidence in receipt["evidence"]}
    print(f"{topic} 引用来源：{'，'.join(sorted(sources))}", flush=True)
    record_id = receipts[-1]["recordId"]
    record_path = f"/v1/records/{record_id}"
    basic = request(client, "GET", record_path)
    score = sum(receipt["isCorrect"] for receipt in receipts)
    assert basic["totalQuestions"] == total_questions and basic["correctCount"] == score
    assert basic["takeaways"] and len(basic["answers"]) == total_questions
    print(f"{topic} 完成 {total_questions} 题，答对 {score} 题；开始等待真实总结", flush=True)
    complete = wait_for(client, record_path, timeout, field="aiSummary")
    summary = complete["aiSummary"]["content"]
    assert summary["knowledgeReview"] and summary["reviewSuggestions"]
    wrong_ids = {
        receipt["question"]["questionId"] for receipt in receipts if not receipt["isCorrect"]
    }
    assert bool(summary["mistakeNotes"]) is bool(wrong_ids)
    assert all(set(note["questionIds"]) <= wrong_ids for note in summary["mistakeNotes"])
    assert any(
        item["recordId"] == record_id for item in request(client, "GET", "/v1/records")["items"]
    )
    print(f"{topic} 真实出题、作答、总结、历史回看通过", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument("--topic", action="append", required=True)
    parser.add_argument("--timeout", type=float, default=720)
    parser.add_argument("--expect-saved-topic", action="store_true")
    args = parser.parse_args()
    with httpx.Client(base_url=args.api, timeout=15, trust_env=False) as client:
        session = request(client, "POST", "/v1/dev/auth", json={"deviceId": f"smoke-{uuid4().hex}"})
        client.headers["Authorization"] = f"Bearer {session['accessToken']}"
        for topic in args.topic:
            run_topic(client, topic, args.timeout, expect_saved=args.expect_saved_topic)


if __name__ == "__main__":
    main()
