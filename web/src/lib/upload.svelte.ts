/**
 * Upload of documents: several files, a few at a time, one call per file. The state of each file
 * follows its document from the answer of the API to its lane, through the event stream.
 */
import { api } from '#lib/api/client.ts';
import { apiError } from '#lib/api/problem.ts';
import type { components } from '#lib/api/schema.ts';
import { describeError } from '#lib/errors.ts';
import { events } from '#lib/events.svelte.ts';

type Lane = components['schemas']['Lane'];
export type UploadState = 'waiting' | 'uploading' | 'processing' | 'done' | 'failed';

export interface Upload {
	key: number;
	name: string;
	state: UploadState;
	documentId: string | null;
	lane: Lane | null;
	message: string | null;
}

const PARALLEL = 3;

export class Uploads {
	items = $state<Upload[]>([]);
	#queue: { upload: Upload; file: File; drawerId: string | null }[] = [];
	#running = 0;
	#next = 1;
	#stop: (() => void) | null = null;
	// eslint-disable-next-line svelte/prefer-svelte-reactivity
	#settled = new Set<() => void>();

	get active(): boolean {
		return this.items.some((item) => ['waiting', 'uploading', 'processing'].includes(item.state));
	}

	/** Call `handler` when an uploaded document has reached its lane; returns the way to stop. */
	onSettled(handler: () => void): () => void {
		this.#settled.add(handler);
		return () => this.#settled.delete(handler);
	}

	#listen(): void {
		if (this.#stop) return;
		this.#stop = events.subscribe((event) => {
			const item = this.items.find((entry) => entry.documentId === event.document_id);
			if (!item || item.state !== 'processing') return;
			if (event.type === 'document.lane_changed' || event.type === 'document.filed') {
				void this.#refreshLane(item).then(() => this.#settled.forEach((handler) => handler()));
			}
		});
	}

	add(files: readonly File[], drawerId: string | null): void {
		this.#listen();
		for (const file of files) {
			const upload: Upload = {
				key: this.#next++,
				name: file.name,
				state: 'waiting',
				documentId: null,
				lane: null,
				message: null
			};
			this.items.push(upload);
			// The pushed item is a proxy; keep working on that one so the page sees the changes.
			this.#queue.push({ upload: this.items[this.items.length - 1], file, drawerId });
		}
		this.#pump();
	}

	clearFinished(): void {
		this.items = this.items.filter((item) => item.state !== 'done' && item.state !== 'failed');
	}

	#pump(): void {
		while (this.#running < PARALLEL && this.#queue.length > 0) {
			const job = this.#queue.shift()!;
			this.#running++;
			void this.#send(job.upload, job.file, job.drawerId).finally(() => {
				this.#running--;
				this.#pump();
			});
		}
	}

	async #send(upload: Upload, file: File, drawerId: string | null): Promise<void> {
		upload.state = 'uploading';
		const form = new FormData();
		form.append('file', file);
		if (drawerId) form.append('drawer_id', drawerId);
		try {
			const { data, error, response } = await api.POST('/api/v1/documents', {
				// The generated type wants a string for the file; the form carries the binary.
				body: { file: '' },
				bodySerializer: () => form
			});
			if (!data) throw await apiError(response, error);
			upload.documentId = data.id;
			upload.state = 'processing';
			await this.#refreshLane(upload);
		} catch (error) {
			upload.state = 'failed';
			upload.message = describeError(error);
		}
	}

	async #refreshLane(upload: Upload): Promise<void> {
		if (!upload.documentId) return;
		const { data } = await api.GET('/api/v1/documents/{id}', {
			params: { path: { id: upload.documentId } }
		});
		if (!data) return;
		upload.lane = data.lane;
		if (data.lane !== null) upload.state = 'done';
	}
}

export const uploads = new Uploads();
