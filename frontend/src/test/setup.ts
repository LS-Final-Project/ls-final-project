import { configure } from '@testing-library/react';

// Cloud Build의 웹 이미지 빌드는 로컬·CI보다 느려 findBy 기본 1초 안에 화면이 다 그려지지 않을 때가 있다
// (2026-11-05 배포 빌드에서 rfp-workspace·CsHome 시험이 시간 초과로 실패). 기다리는 한도만 늘린다.
configure({ asyncUtilTimeout: 5000 });
