import { apiError } from './problem.ts';

interface Result<T> {
	data?: T;
	error?: unknown;
	response: Response;
}

/** The data of a call; an ApiError for any answer that is not a success. */
export async function unwrap<T>(call: Promise<Result<T>>): Promise<T> {
	const { data, error, response } = await call;
	if (!response.ok) throw await apiError(response, error);
	return data as T;
}
