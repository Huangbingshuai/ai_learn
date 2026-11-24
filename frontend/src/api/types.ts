import type { paths } from './schema'

type SuccessResponse<T> = T extends { responses: infer R }
  ? {
    [K in keyof R & (200 | 201 | 202)]: R[K] extends { content: { 'application/json': infer Item } } ? Item : never
  }[keyof R & (200 | 201 | 202)]
  : never

type For<P extends keyof paths, M extends keyof paths[P]> = SuccessResponse<paths[P][M]>

export type BodyFor<P extends keyof paths, M extends keyof paths[P]> = paths[P][M] extends {
  requestBody: { content: { 'application/json': infer Body } }
} ? Body : never

export type Session = For<'/v1/auth/wechat', 'post'>

export type TopicList = For<'/v1/topics', 'get'>
export type Topic = TopicList['items'][number]
export type GenerationJob = For<'/v1/generation-jobs', 'post'>
export type Attempt = For<'/v1/attempts', 'post'>
export type QuizGroup = Pick<Attempt, 'quizId' | 'quizExpiresAt'>
export type Question = NonNullable<Attempt['question']>
export type AnswerReceipt = For<'/v1/attempts/{attempt_id}/answers/{question_id}', 'put'>
export type Evidence = AnswerReceipt['evidence'][number]
export type RecordPage = For<'/v1/records', 'get'>
export type RecordItem = RecordPage['items'][number]
export type LearningRecord = For<'/v1/records/{record_id}', 'get'>
export type AiSummary = LearningRecord['aiSummary']
export type SummaryEntry = NonNullable<AiSummary['content']>['knowledgeReview'][number]
