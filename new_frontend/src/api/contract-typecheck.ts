import type {
  DemoLoginApiV1AuthDemoPostResponse,
  GetUsageApiV1UsageGetResponse,
  GetSessionApiV1cSessionIdGetResponse,
  ListProjectsApiV1gGetResponse,
  ListSessionsApiV1gProjectKeyCGetResponse,
} from "./generated/types.gen";
import type { ResearchApi } from "./types";

type Assert<T extends true> = T;
type _Login = Assert<DemoLoginApiV1AuthDemoPostResponse extends Awaited<ReturnType<ResearchApi["loginDemo"]>> ? true : false>;
type _Usage = Assert<GetUsageApiV1UsageGetResponse extends Awaited<ReturnType<ResearchApi["getUsage"]>> ? true : false>;
type _Projects = Assert<ListProjectsApiV1gGetResponse extends Awaited<ReturnType<ResearchApi["getProjects"]>> ? true : false>;
type _Sessions = Assert<ListSessionsApiV1gProjectKeyCGetResponse extends Awaited<ReturnType<ResearchApi["getSessions"]>> ? true : false>;
type _Session = Assert<GetSessionApiV1cSessionIdGetResponse extends Awaited<ReturnType<ResearchApi["getSession"]>> ? true : false>;

export type ContractTypecheck = [_Login, _Usage, _Projects, _Sessions, _Session];
